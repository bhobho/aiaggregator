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


def summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    window = (f"-{days} days",)

    def q(sql, params=window):
        return conn.execute(sql, params).fetchall()

    totals = conn.execute(
        """SELECT COUNT(*) visits, COUNT(DISTINCT ip) visitors
           FROM visits WHERE ts >= datetime('now', ?)""", window
    ).fetchone()

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

    recent = conn.execute(
        """SELECT v.ts, v.ip, v.path, v.user_agent,
                  COALESCE(g.city,'') city, COALESCE(g.region,'') region,
                  COALESCE(g.country,'') country
           FROM visits v LEFT JOIN ip_geo g ON g.ip = v.ip
           ORDER BY v.ts DESC LIMIT 100""").fetchall()

    return {
        "days": days,
        "visits": totals["visits"] or 0,
        "visitors": totals["visitors"] or 0,
        "by_country": by_country,
        "by_city": by_city,
        "by_page": by_page,
        "recent": recent,
    }
