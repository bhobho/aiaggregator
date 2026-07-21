"""Visitor analytics: geo resolution and aggregate queries.

Geo lookup uses the free, no-key ip-api.com service for public IPs only. Private /
loopback addresses are labelled "Local" without any external call.
"""
from __future__ import annotations

import ipaddress
import logging
import sqlite3
from datetime import date, timedelta

import httpx

from . import db

log = logging.getLogger(__name__)


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
    rows = conn.execute(
        """SELECT CAST((julianday(date('now')) - julianday(substr(ts,1,10))) / ? AS INT) off,
                  COUNT(DISTINCT ip) u
           FROM visits WHERE ts >= datetime('now', ?)
           GROUP BY off""",
        (unit_days, f"-{unit_days * count} days"),
    ).fetchall()
    m = {int(r["off"]): r["u"] for r in rows if r["off"] is not None}
    series = [{"label": label_of(off), "value": m.get(off, 0)}
              for off in range(count - 1, -1, -1)]  # oldest → newest
    return _scale(series)


def _monthly_series(conn: sqlite3.Connection, months: int = 6) -> list[dict]:
    rows = conn.execute(
        """SELECT substr(ts,1,7) m, COUNT(DISTINCT ip) u
           FROM visits WHERE ts >= datetime('now', ?) GROUP BY m""",
        (f"-{months} months",),
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


def summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    window = (f"-{days} days",)

    def q(sql, params=window):
        return conn.execute(sql, params).fetchall()

    totals = conn.execute(
        """SELECT COUNT(*) visits, COUNT(DISTINCT ip) visitors
           FROM visits WHERE ts >= datetime('now', ?)""", window
    ).fetchone()

    today = date.today()
    daily = _offset_series(conn, 1, 14,
                           lambda off: (lambda d: f"{d.month}/{d.day}")(today - timedelta(days=off)))
    weekly = _offset_series(conn, 7, 8,
                            lambda off: (lambda d: f"{d.month}/{d.day}")(today - timedelta(days=off * 7)))
    monthly = _monthly_series(conn, 6)

    # Location of unique views (distinct visitors per place).
    by_country = q(
        """SELECT COALESCE(g.country,'Unknown') country, COUNT(DISTINCT v.ip) uniques
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           WHERE v.ts >= datetime('now', ?)
           GROUP BY country ORDER BY uniques DESC LIMIT 10""")

    by_city = q(
        """SELECT COALESCE(g.city,'') city, COALESCE(g.country,'Unknown') country,
                  COUNT(DISTINCT v.ip) uniques
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           WHERE v.ts >= datetime('now', ?) AND COALESCE(g.city,'') != ''
           GROUP BY city, country ORDER BY uniques DESC LIMIT 10""")

    return {
        "days": days,
        "visits": totals["visits"] or 0,
        "visitors": totals["visitors"] or 0,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "by_country": by_country,
        "by_city": by_city,
    }
