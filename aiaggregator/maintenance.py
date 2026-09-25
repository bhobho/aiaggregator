"""Database upkeep: keeps the SQLite file lean.

Runs inside the app process, nightly and once shortly after startup (see
main.py), on a worker thread so the heavier steps don't stall page loads. It
must never run from a separate process while the app is up: two uncoordinated
writers on the same file is how the search index got corrupted in Sep 2026.

Each pass:
  1. clears stored title embeddings once an article is past the clustering
     window (they're only used to group the same story across outlets),
  2. deletes articles that never got enriched and are past the retention age
     (every summarised section of the site already ignores them),
  3. drops the stored full text of other outlets' older articles (their post
     page falls back to the summary plus a link to the original),
  4. checks the search index against the articles table and rebuilds it if
     they disagree (it's derived data, so a rebuild loses nothing),
  5. compacts the search index and returns free pages to the filesystem.

The owner's own posts (My Page sources) are never deleted or slimmed.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from . import db, queries
from .config import settings

log = logging.getLogger(__name__)

BATCH = 500  # rows per write transaction, so the app's other writers never wait long
_AUTO_VACUUM_INCREMENTAL = 2


def _owner_source_ids(conn: sqlite3.Connection) -> list[int]:
    names = sorted(queries.MY_SOURCES | {queries.MY_LINKEDIN_SOURCE})
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT id FROM sources WHERE name IN ({marks}) OR url = ?",
        (*names, queries.OWN_BLOG_SOURCE_URL),
    ).fetchall()
    return [r[0] for r in rows]


def _ids(conn: sqlite3.Connection, where: str, params: tuple) -> list[int]:
    return [r[0] for r in conn.execute(f"SELECT id FROM articles WHERE {where}", params)]


def _in_batches(conn: sqlite3.Connection, ids: list[int], sql: str) -> int:
    for i in range(0, len(ids), BATCH):
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(sql, [(x,) for x in ids[i:i + BATCH]])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return len(ids)


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    # OperationalError ("database is locked", ...) is also a DatabaseError; only
    # an actual disagreement (SQLITE_CORRUPT*) should trigger a rebuild.
    name = getattr(exc, "sqlite_errorname", "") or ""
    return name.startswith("SQLITE_CORRUPT") or "malformed" in str(exc)


def _check_search_index(conn: sqlite3.Connection) -> str:
    try:
        # rank=1: also compare every index entry with the articles table.
        conn.execute("INSERT INTO articles_fts(articles_fts, rank) VALUES('integrity-check', 1)")
        return "ok"
    except sqlite3.DatabaseError as exc:
        if not _is_corruption(exc):
            raise
        log.error("search index disagrees with the articles table; rebuilding it")
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        return "rebuilt"


def _reclaim_space(conn: sqlite3.Connection) -> str:
    # Merge the search index's segments (every article insert/update adds one).
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('optimize')")
    if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != _AUTO_VACUUM_INCREMENTAL:
        # One-time switch: a new auto_vacuum mode only takes effect after a full
        # VACUUM. From then on each pass frees pages cheaply and incrementally.
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("VACUUM")
        how = "full vacuum (one-time switch to incremental)"
    else:
        # fetchall(): the pragma frees one page per step, so run it to completion.
        conn.execute("PRAGMA incremental_vacuum").fetchall()
        how = "incremental vacuum"
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return how


def run(db_path: Path | None = None) -> dict:
    """One maintenance pass. Blocking — call via asyncio.to_thread from the app."""
    t0 = time.monotonic()
    conn = db.connect(db_path)
    conn.isolation_level = None          # explicit transactions; VACUUM needs autocommit
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        owners = _owner_source_ids(conn)
        not_owner = f"source_id NOT IN ({','.join('?' * len(owners))})"
        res: dict = {}

        # Same date expression as db.recent_articles (the clustering window), with
        # a margin, so an embedding still in use is never cleared.
        emb_days = max(settings.embedding_keep_days, settings.cluster_window_days + 1)
        ids = _ids(conn, "embedding IS NOT NULL AND julianday(COALESCE(published_at, "
                         "fetched_at)) < julianday('now', ?)", (f"-{emb_days} days",))
        res["embeddings_cleared"] = _in_batches(
            conn, ids, "UPDATE articles SET embedding=NULL WHERE id=?")

        # fetched_at (when we stored it), not published_at: some feeds carry
        # bogus publish dates (1970), which would make fresh items look ancient.
        ids = _ids(conn, f"status='failed' AND julianday(fetched_at) < julianday('now', ?) "
                         f"AND {not_owner}",
                   (f"-{settings.failed_retention_days} days", *owners))
        res["failed_deleted"] = _in_batches(conn, ids, "DELETE FROM articles WHERE id=?")

        ids = _ids(conn, f"COALESCE(content, '') != '' AND julianday(fetched_at) < "
                         f"julianday('now', ?) AND {not_owner}",
                   (f"-{settings.content_retention_days} days", *owners))
        res["content_cleared"] = _in_batches(
            conn, ids, "UPDATE articles SET content=NULL WHERE id=?")

        res["search_index"] = _check_search_index(conn)
        res["space"] = _reclaim_space(conn)
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        size = conn.execute("PRAGMA page_size").fetchone()[0]
        res["file_mb"] = round(pages * size / 1048576, 1)
        res["seconds"] = round(time.monotonic() - t0, 1)
        log.info("db maintenance: %s", res)
        return res
    finally:
        conn.close()
