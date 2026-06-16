"""Orchestrate fetch -> normalize -> store across all sources."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import httpx

from .. import db
from ..feeds import load_feeds
from .fetcher import fetch_feed
from .normalize import parse_feed

log = logging.getLogger(__name__)


def sync_sources(conn: sqlite3.Connection) -> None:
    """Make the DB sources table reflect feeds.yaml.

    Sources removed from the YAML are pruned: their articles are deleted and the
    source row removed, so dropped feeds (e.g. arXiv, Reddit) stop appearing.
    """
    sources, _ = load_feeds()
    keep_urls = {s.url for s in sources}
    for src in sources:
        db.upsert_source(conn, src)
    db.prune_sources(conn, keep_urls)


async def _ingest_one(client: httpx.AsyncClient, conn: sqlite3.Connection, src,
                      keywords: list[str]) -> int:
    res = await fetch_feed(client, src.url, etag=src.etag, last_modified=src.last_modified)
    if res.status == "not_modified":
        db.update_source_fetch(conn, src.id, etag=src.etag,
                               last_modified=src.last_modified, status="ok (not modified)")
        return 0
    if res.status.startswith("error") or res.body is None:
        db.update_source_fetch(conn, src.id, etag=src.etag,
                               last_modified=src.last_modified, status=res.status)
        log.warning("fetch failed for %s: %s", src.name, res.status)
        return 0

    articles = parse_feed(
        res.body, src.id,
        is_community=(src.category == "community"), keywords=keywords,
    )
    new = 0
    for a in articles:
        if db.insert_article(conn, a) is not None:
            new += 1
    db.update_source_fetch(conn, src.id, etag=res.etag,
                           last_modified=res.last_modified, status="ok")
    log.info("%s: %d new (%d parsed)", src.name, new, len(articles))
    return new


async def run_ingest(conn: sqlite3.Connection) -> int:
    """Fetch all active sources concurrently; return count of new articles."""
    _, keywords = load_feeds()
    sources = db.list_sources(conn, active_only=True)
    total = 0
    limits = httpx.Limits(max_connections=8)
    async with httpx.AsyncClient(limits=limits) as client:
        results = await asyncio.gather(
            *(_ingest_one(client, conn, s, keywords) for s in sources),
            return_exceptions=True,
        )
    for r in results:
        if isinstance(r, Exception):
            log.error("ingest task error: %s", r)
        else:
            total += r
    return total
