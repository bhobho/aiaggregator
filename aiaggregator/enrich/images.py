"""Backfill lead images for articles whose feed item didn't supply one.

Runs as a periodic scheduler job (see main.py), same pattern as enrichment:
pull a small batch of never-checked articles, fetch og:image for each
concurrently, and record the result (found or not) so nothing is re-tried
forever.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import httpx

from .. import db
from ..ingest.pagemeta import fetch_lead_image
from ..models import Article

log = logging.getLogger(__name__)


async def _backfill_one(client: httpx.AsyncClient, conn: sqlite3.Connection,
                        article: Article, found: list[int]) -> None:
    image = await fetch_lead_image(client, article.url)
    if image:
        db.backfill_image(conn, article.source_id, article.guid, image)
        found.append(article.id)
    else:
        db.mark_no_image(conn, article.id)


async def run_image_backfill(conn: sqlite3.Connection, limit: int) -> int:
    """Look up a lead image for up to `limit` articles missing one.
    Returns how many were found."""
    pending = db.pending_image_backfill(conn, limit)
    if not pending:
        return 0
    found: list[int] = []
    limits = httpx.Limits(max_connections=6)
    async with httpx.AsyncClient(limits=limits) as client:
        results = await asyncio.gather(
            *(_backfill_one(client, conn, a, found) for a in pending),
            return_exceptions=True,
        )
    for r in results:
        if isinstance(r, Exception):
            log.warning("image backfill error: %s", r)
    if found:
        log.info("image backfill: found %d/%d", len(found), len(pending))
    return len(found)
