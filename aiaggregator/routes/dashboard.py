"""Dashboard routes: feed, filtered/searched partials, digest."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import db, queries, vendors as vendormod
from ..enrich import digest as digest_mod

router = APIRouter()


def _to_int(v) -> int | None:
    """Coerce a query value to int, treating '' / invalid as None.

    Form selects submit '' for the 'Any' option; FastAPI int params would 422 on
    that, so filters are parsed as strings here and coerced safely.
    """
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _filters(company, category, days, min_importance, sort) -> queries.FeedFilters:
    return queries.FeedFilters(
        company=company or None,
        category=category or None,
        days=_to_int(days),
        min_importance=_to_int(min_importance),
        sort=sort or "newest",
    )


def _feed_context(request: Request, f: queries.FeedFilters) -> dict:
    conn = db.connect()
    try:
        articles = queries.feed(conn, f)
        groups = queries.group_clusters(articles)
        srcmap = queries.source_name_map(conn)
        return {
            "request": request,
            "groups": groups,
            "srcmap": srcmap,
            "stats": queries.stats(conn),
            "top_headlines": queries.top_headlines(conn, limit=8),
            "filters": f,
        }
    finally:
        conn.close()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Filters were removed from the UI; the homepage shows the composite-ranked feed
    # (see ranking.py) so the most important stories lead.
    f = queries.FeedFilters(sort="importance")
    ctx = _feed_context(request, f)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/feed", response_class=HTMLResponse)
async def feed_partial(
    request: Request,
    company: str = "",
    category: str = "",
    days: str = "",
    min_importance: str = "",
    sort: str = "newest",
):
    """HTMX partial: just the list of cards."""
    f = _filters(company, category, days, min_importance, sort)
    ctx = _feed_context(request, f)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "_list.html", ctx)


@router.get("/enterprises", response_class=HTMLResponse)
async def vendors_view(request: Request):
    conn = db.connect()
    try:
        tiles = queries.vendor_tiles(conn)
        srcmap = queries.source_name_map(conn)
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "vendors.html", {"tiles": tiles, "srcmap": srcmap}
    )


@router.get("/enterprise/{slug}", response_class=HTMLResponse)
async def vendor_view(request: Request, slug: str):
    vendor = vendormod.BY_SLUG.get(slug)
    conn = db.connect()
    try:
        if vendor is None:
            articles = []
        else:
            articles = queries.vendor_feed(conn, slug)
        groups = queries.group_clusters(articles)
        srcmap = queries.source_name_map(conn)
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "vendor.html",
        {"vendor": vendor, "groups": groups, "srcmap": srcmap, "count": len(articles)},
    )


@router.get("/digest", response_class=HTMLResponse)
async def digest_view(request: Request, date: str = ""):
    conn = db.connect()
    try:
        current = digest_mod.get_digest(conn, date or None)
        dates = digest_mod.list_digest_dates(conn)
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "digest.html",
        {"digest": current, "dates": dates},
    )
