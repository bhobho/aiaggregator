"""Build a daily markdown digest of the top stories."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

from .. import db, queries
from ..models import now_iso

log = logging.getLogger(__name__)


def build_digest(conn: sqlite3.Connection, for_date: str | None = None,
                 top_n: int = 12) -> str:
    day = for_date or date.today().isoformat()
    # Pull a slightly larger ranked pool so cluster-dedup still yields top_n.
    enriched = queries.ranked_enriched(conn, days=1, limit=top_n * 3)

    lines = [f"# AI News Digest — {day}", ""]
    if not enriched:
        lines.append("_No enriched articles in the last 24h yet._")
        return "\n".join(lines)

    seen_clusters: set[int] = set()
    rank = 0
    for a in enriched:
        if a.cluster_id is not None:
            if a.cluster_id in seen_clusters:
                continue
            seen_clusters.add(a.cluster_id)
        rank += 1
        if rank > top_n:
            break
        tags = " ".join(f"`{t}`" for t in a.tags)
        comp = ", ".join(a.companies)
        meta = " · ".join(filter(None, [comp, tags]))
        lines.append(f"### {rank}. [{a.title}]({a.url})")
        lines.append(f"*importance {a.importance}* {('· ' + meta) if meta else ''}")
        lines.append("")
        lines.append(a.summary or a.raw_summary)
        lines.append("")
    return "\n".join(lines)


def save_digest(conn: sqlite3.Connection, for_date: str | None = None) -> str:
    day = for_date or date.today().isoformat()
    md = build_digest(conn, day)
    conn.execute(
        """INSERT INTO digests (date, markdown, created_at) VALUES (?,?,?)
           ON CONFLICT(date) DO UPDATE SET markdown=excluded.markdown,
               created_at=excluded.created_at""",
        (day, md, now_iso()),
    )
    conn.commit()
    log.info("digest saved for %s", day)
    return md


def get_digest(conn: sqlite3.Connection, day: str | None = None) -> tuple[str, str] | None:
    if day:
        row = conn.execute("SELECT date, markdown FROM digests WHERE date=?", (day,)).fetchone()
    else:
        row = conn.execute("SELECT date, markdown FROM digests ORDER BY date DESC LIMIT 1").fetchone()
    return (row["date"], row["markdown"]) if row else None


def list_digest_dates(conn: sqlite3.Connection) -> list[str]:
    return [r["date"] for r in conn.execute("SELECT date FROM digests ORDER BY date DESC")]
