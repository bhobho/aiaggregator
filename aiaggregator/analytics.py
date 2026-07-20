"""Visitor analytics: geo resolution and aggregate queries.

Geo lookup uses the free, no-key ip-api.com service for public IPs only. Private /
loopback addresses are labelled "Local" without any external call.
"""
from __future__ import annotations

import ipaddress
import logging
import sqlite3

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


def _weekly_trend(conn: sqlite3.Connection, weeks: int = 8) -> list[dict]:
    """Views per week for the last `weeks` weeks, oldest → newest.

    Buckets by calendar-day distance from today (robust to the ISO/offset ts
    format), so week 0 is the current rolling 7-day window.
    """
    from datetime import date

    rows = conn.execute(
        """SELECT substr(ts, 1, 10) d, COUNT(*) hits
           FROM visits WHERE ts >= datetime('now', ?)
           GROUP BY d""",
        (f"-{weeks * 7} days",),
    ).fetchall()

    today = date.today()
    buckets = [0] * weeks
    for r in rows:
        try:
            day = date.fromisoformat(r["d"])
        except (ValueError, TypeError):
            continue
        idx = (today - day).days // 7
        if 0 <= idx < weeks:
            buckets[idx] += r["hits"]

    ordered = list(reversed(buckets))  # oldest first
    peak = max(ordered) or 1
    out = []
    for i, views in enumerate(ordered):
        ago = weeks - 1 - i
        out.append({
            "label": "This week" if ago == 0 else f"{ago}w ago",
            "views": views,
            "bar": round(views / peak * 100),
        })
    return out


def summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    window = (f"-{days} days",)

    def q(sql, params=window):
        return conn.execute(sql, params).fetchall()

    totals = conn.execute(
        """SELECT COUNT(*) visits, COUNT(DISTINCT ip) visitors
           FROM visits WHERE ts >= datetime('now', ?)""", window
    ).fetchone()

    # Rolling weekly windows: current 7 days vs the prior 7 days (for the trend %).
    this_week = conn.execute(
        """SELECT COUNT(*) visits, COUNT(DISTINCT ip) visitors
           FROM visits WHERE ts >= datetime('now', '-7 days')""").fetchone()
    prev_week = conn.execute(
        """SELECT COUNT(*) visits FROM visits
           WHERE ts >= datetime('now', '-14 days') AND ts < datetime('now', '-7 days')"""
    ).fetchone()

    weekly_views = this_week["visits"] or 0
    prev_views = prev_week["visits"] or 0
    if prev_views:
        wow = round((weekly_views - prev_views) / prev_views * 100)
    else:
        wow = 100 if weekly_views else 0
    wow = max(-100, min(999, wow))  # clamp: a low-traffic prior week skews the ratio

    by_country = q(
        """SELECT COALESCE(g.country,'Unknown') country, COUNT(*) hits,
                  COUNT(DISTINCT v.ip) visitors
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           WHERE v.ts >= datetime('now', ?)
           GROUP BY country ORDER BY hits DESC LIMIT 25""")

    by_city = q(
        """SELECT COALESCE(g.city,'') city, COALESCE(g.country,'Unknown') country,
                  COUNT(*) hits, COUNT(DISTINCT v.ip) visitors
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           WHERE v.ts >= datetime('now', ?) AND COALESCE(g.city,'') != ''
           GROUP BY city, country ORDER BY hits DESC LIMIT 25""")

    by_page = q(
        """SELECT path, COUNT(*) hits FROM visits
           WHERE ts >= datetime('now', ?) GROUP BY path ORDER BY hits DESC LIMIT 25""")

    return {
        "days": days,
        "visits": totals["visits"] or 0,
        "visitors": totals["visitors"] or 0,
        "weekly_views": weekly_views,
        "weekly_unique": this_week["visitors"] or 0,
        "wow": wow,
        "trend": _weekly_trend(conn),
        "by_country": by_country,
        "by_city": by_city,
        "by_page": by_page,
    }
