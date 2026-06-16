"""Admin/health routes: source status, manual refresh, digest rebuild."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import db, queries
from ..enrich import ollama_client

router = APIRouter()


@router.get("/health", response_class=HTMLResponse)
async def health(request: Request):
    conn = db.connect()
    try:
        sources = db.list_sources(conn, active_only=False)
        counts = db.source_counts(conn)
        stats = queries.stats(conn)
    finally:
        conn.close()
    ollama_up = await ollama_client.is_available()
    models = await ollama_client.list_models() if ollama_up else []
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "health.html",
        {
            "sources": sources,
            "counts": counts,
            "stats": stats,
            "ollama_up": ollama_up,
            "models": models,
        },
    )


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request):
    """Fetch all feeds now, then run one enrichment + cluster pass."""
    jobs = request.app.state.jobs
    await jobs["fetch"]()
    await jobs["enrich"]()
    conn = db.connect()
    try:
        stats = queries.stats(conn)
    finally:
        conn.close()
    return HTMLResponse(
        f'<div class="rounded bg-emerald-50 border border-emerald-300 text-emerald-800 px-3 py-2 text-sm">'
        f'Refreshed — {stats["total"]} articles, {stats["enriched"]} summarized, '
        f'{stats["pending"]} pending, {stats["clusters"]} clusters.</div>'
    )
