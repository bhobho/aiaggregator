"""Visitor analytics: geo resolution and aggregate queries.

Geo lookup uses the free, no-key ip-api.com service for public IPs only. Private /
loopback addresses are labelled "Local" without any external call.
"""
from __future__ import annotations

import ipaddress
import logging
import sqlite3
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx

from . import db
from .config import settings

log = logging.getLogger(__name__)


def _excl_clause(alias: str) -> tuple[str, list]:
    """SQL fragment + params excluding the owner's own IP(s) from a query.
    Returns ("", []) when no IPs are configured."""
    ips = sorted(settings.analytics_exclude_ip_set)
    if not ips:
        return "", []
    marks = ",".join("?" * len(ips))
    return f" AND {alias}.ip NOT IN ({marks})", ips


def _fmt_duration(ms: float | None) -> str:
    if not ms or ms <= 0:
        return "—"
    secs = round(ms / 1000)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60:02d}s"


# Paths are bucketed into the same sections shown in the nav, so "time spent"
# reads as "time spent on Tech News" rather than a wall of individual URLs.
_SECTIONS: list[tuple[str, str]] = [
    ("/post/", "Article pages"),
    ("/market", "AI News"),
    ("/tech", "Tech News"),
    ("/industry", "Industry View"),
    ("/architecture", "Architecture"),
    ("/blogs", "Blogs"),
    ("/podcasts", "Podcasts"),
]


def _section(path: str) -> str:
    if path == "/":
        return "My Page"
    for prefix, label in _SECTIONS:
        if path.startswith(prefix):
            return label
    return "Other"


# Recognizable inbound traffic sources, matched against the referer's hostname.
_KNOWN_SOURCES: list[tuple[str, str]] = [
    ("google.", "Google"),
    ("bing.", "Bing"),
    ("duckduckgo.", "DuckDuckGo"),
    ("yahoo.", "Yahoo"),
    ("linkedin.", "LinkedIn"),
    ("lnkd.in", "LinkedIn"),
    ("t.co", "Twitter/X"),
    ("twitter.", "Twitter/X"),
    ("x.com", "Twitter/X"),
    ("facebook.", "Facebook"),
    ("reddit.", "Reddit"),
    ("news.ycombinator", "Hacker News"),
    ("medium.com", "Medium"),
    ("hashnode.", "Hashnode"),
    ("chat.openai.com", "ChatGPT"),
    ("perplexity.ai", "Perplexity"),
]

_LOCAL_HOSTS = {"localhost", "127.0.0.1"}
# This deployment's own domain — used to recognize same-site navigation as
# "Internal" even when AIAGG_PUBLIC_URL isn't set locally (see .env.example).
_DEFAULT_OWN_HOST = "ainews.codenlearn.in"


def _own_hostname() -> str:
    if settings.public_url:
        try:
            host = (urlparse(settings.public_url).hostname or "").lower()
            if host:
                return host
        except ValueError:
            pass
    return _DEFAULT_OWN_HOST


def _traffic_source(referer: str | None) -> str:
    """Classify a referer URL into a readable source label. Same-site
    navigation (clicking between our own pages) isn't an external traffic
    source, so it's labelled 'Internal' and dropped from the report."""
    if not referer:
        return "Direct"
    try:
        host = (urlparse(referer).hostname or "").lower()
    except ValueError:
        return "Direct"
    if not host:
        return "Direct"
    host = host.removeprefix("www.")
    if host in _LOCAL_HOSTS or host == _own_hostname():
        return "Internal"
    for needle, label in _KNOWN_SOURCES:
        if needle in host:
            return label
    return host


def _is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as local/unknown
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


async def _lookup(client: httpx.AsyncClient, ip: str) -> tuple[str, str, str]:
    """Return (country, region, city) for a public IP; ('Local','','') for private."""
    if _is_private(ip):
        return ("Local", "", "")
    try:
        r = await client.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,regionName,city"},
            timeout=5.0,
        )
        data = r.json()
        if data.get("status") == "success":
            return (data.get("country") or "Unknown",
                    data.get("regionName") or "",
                    data.get("city") or "")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("geo lookup failed for %s: %s", ip, exc)
    return ("Unknown", "", "")


async def resolve_pending(conn: sqlite3.Connection, limit: int = 50) -> int:
    """Resolve geo for visitor IPs that aren't cached yet. Best-effort."""
    ips = db.unresolved_ips(conn, limit=limit)
    if not ips:
        return 0
    done = 0
    async with httpx.AsyncClient() as client:
        for ip in ips:
            country, region, city = await _lookup(client, ip)
            db.save_geo(conn, ip, country, region, city)
            done += 1
    return done


def _scale(series: list[dict]) -> list[dict]:
    """Add a 0-100 bar height (relative to the busiest bucket) to each point."""
    peak = max((s["value"] for s in series), default=0) or 1
    for s in series:
        s["bar"] = round(s["value"] / peak * 100)
    return series


def _offset_series(conn: sqlite3.Connection, unit_days: int, count: int,
                   label_of) -> list[dict]:
    """Unique visitors bucketed by integer period offset from today (0 = current
    period). One helper serves daily (unit_days=1) and weekly (unit_days=7);
    julianday works on the substr'd date, so the ISO/offset ts format is safe."""
    excl, params = _excl_clause("v")
    rows = conn.execute(
        f"""SELECT CAST((julianday(date('now')) - julianday(substr(ts,1,10))) / ? AS INT) off,
                  COUNT(DISTINCT ip) u
           FROM visits v WHERE ts >= datetime('now', ?){excl}
           GROUP BY off""",
        [unit_days, f"-{unit_days * count} days", *params],
    ).fetchall()
    m = {int(r["off"]): r["u"] for r in rows if r["off"] is not None}
    series = [{"label": label_of(off), "value": m.get(off, 0)}
              for off in range(count - 1, -1, -1)]  # oldest → newest
    return _scale(series)


def _monthly_series(conn: sqlite3.Connection, months: int = 6) -> list[dict]:
    excl, params = _excl_clause("v")
    rows = conn.execute(
        f"""SELECT substr(ts,1,7) m, COUNT(DISTINCT ip) u
           FROM visits v WHERE ts >= datetime('now', ?){excl} GROUP BY m""",
        [f"-{months} months", *params],
    ).fetchall()
    m = {r["m"]: r["u"] for r in rows}
    today = date.today()
    base = today.year * 12 + (today.month - 1)
    series = []
    for i in range(months - 1, -1, -1):
        yy, mm0 = divmod(base - i, 12)
        series.append({"label": date(yy, mm0 + 1, 1).strftime("%b"),
                       "value": m.get(f"{yy:04d}-{mm0 + 1:02d}", 0)})
    return _scale(series)


def top_outbound(conn: sqlite3.Connection, days: int = 30, limit: int = 12) -> list[dict]:
    """Destinations visitors are routed to when they click through to the
    original source (Read full story / View original), busiest first."""
    excl, params = _excl_clause("o")
    rows = conn.execute(
        f"""SELECT domain, COUNT(*) clicks, COUNT(DISTINCT ip) visitors
           FROM outbound_clicks o
           WHERE ts >= datetime('now', ?){excl}
           GROUP BY domain ORDER BY clicks DESC LIMIT ?""",
        [f"-{days} days", *params, limit],
    ).fetchall()
    return [{"domain": r["domain"], "clicks": r["clicks"], "visitors": r["visitors"]}
            for r in rows]


def top_referrers(conn: sqlite3.Connection, days: int = 30, limit: int = 12) -> list[dict]:
    """Where visitors are arriving from — search engines, socials, or other
    sites linking in. Same-site navigation and blank referers aren't external
    traffic sources: 'Internal' is dropped, 'Direct' (no referer) is kept."""
    excl, params = _excl_clause("v")
    rows = conn.execute(
        f"""SELECT referer, ip FROM visits v WHERE ts >= datetime('now', ?){excl}""",
        [f"-{days} days", *params],
    ).fetchall()

    buckets: dict[str, dict] = {}
    for r in rows:
        label = _traffic_source(r["referer"])
        if label == "Internal":
            continue
        b = buckets.setdefault(label, {"source": label, "visits": 0, "ips": set()})
        b["visits"] += 1
        b["ips"].add(r["ip"])

    out = [{"source": b["source"], "visits": b["visits"], "visitors": len(b["ips"])}
           for b in buckets.values()]
    out.sort(key=lambda x: x["visits"], reverse=True)
    return out[:limit]


def dwell_summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    """Time-on-page: an overall average plus a breakdown by section, from the
    client-reported beacon (see /beacon/dwell)."""
    excl, params = _excl_clause("e")
    rows = conn.execute(
        f"""SELECT path, duration_ms FROM engagement e
           WHERE ts >= datetime('now', ?){excl}""",
        [f"-{days} days", *params],
    ).fetchall()

    totals_ms = [r["duration_ms"] for r in rows]
    overall_avg = sum(totals_ms) / len(totals_ms) if totals_ms else 0

    per_section: dict[str, list[int]] = {}
    for r in rows:
        per_section.setdefault(_section(r["path"]), []).append(r["duration_ms"])

    by_section = sorted(
        (
            {"section": name, "avg_ms": sum(vals) / len(vals), "n": len(vals),
             "avg_label": _fmt_duration(sum(vals) / len(vals))}
            for name, vals in per_section.items()
        ),
        key=lambda s: s["n"], reverse=True,
    )

    return {
        "avg_ms": overall_avg,
        "avg_label": _fmt_duration(overall_avg),
        "n": len(totals_ms),
        "by_section": by_section,
    }


def summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    window = (f"-{days} days",)
    excl_v, excl_v_params = _excl_clause("v")

    def q(sql, params=window):
        return conn.execute(sql, params).fetchall()

    totals = conn.execute(
        f"""SELECT COUNT(*) visits, COUNT(DISTINCT ip) visitors
           FROM visits v WHERE ts >= datetime('now', ?){excl_v}""",
        [*window, *excl_v_params],
    ).fetchone()

    today = date.today()
    daily = _offset_series(conn, 1, 14,
                           lambda off: (lambda d: f"{d.month}/{d.day}")(today - timedelta(days=off)))
    weekly = _offset_series(conn, 7, 8,
                            lambda off: (lambda d: f"{d.month}/{d.day}")(today - timedelta(days=off * 7)))
    monthly = _monthly_series(conn, 6)

    # Location of unique views (distinct visitors per place).
    by_country = q(
        f"""SELECT COALESCE(g.country,'Unknown') country, COUNT(DISTINCT v.ip) uniques
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           WHERE v.ts >= datetime('now', ?){excl_v}
           GROUP BY country ORDER BY uniques DESC LIMIT 10""",
        [*window, *excl_v_params])

    by_city = q(
        f"""SELECT COALESCE(g.city,'') city, COALESCE(g.country,'Unknown') country,
                  COUNT(DISTINCT v.ip) uniques
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           WHERE v.ts >= datetime('now', ?) AND COALESCE(g.city,'') != ''{excl_v}
           GROUP BY city, country ORDER BY uniques DESC LIMIT 10""",
        [*window, *excl_v_params])

    return {
        "days": days,
        "visits": totals["visits"] or 0,
        "visitors": totals["visitors"] or 0,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "by_country": by_country,
        "by_city": by_city,
        "dwell": dwell_summary(conn, days),
        "outbound": top_outbound(conn, days),
        "referrers": top_referrers(conn, days),
    }
