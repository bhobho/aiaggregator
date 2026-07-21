"""Dashboard routes: feed and filtered/searched partials."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import db, market as marketmod, queries, ticker as tickermod, vendors as vendormod
from ..enrich import summarize as summarize_mod

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


def _feed_context(request: Request, f: queries.FeedFilters, *,
                  heading: str | None = None, sub: str | None = None,
                  chips: list | None = None) -> dict:
    conn = db.connect()
    try:
        articles = queries.dedupe_stories(queries.feed(conn, f))
        groups = queries.group_clusters(articles)
        srcmap = queries.source_name_map(conn)
        return {
            "request": request,
            "groups": groups,
            "srcmap": srcmap,
            "stats": queries.stats(conn),
            "top_headlines": queries.top_headlines(conn, limit=8),
            "voices": queries.voices_latest(conn, limit=6),
            "filters": f,
            "heading": heading,
            "sub": sub,
            "chips": chips,
        }
    finally:
        conn.close()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Home: a balanced mix of the latest from AI News, Tech News, Blogs,
    # Architecture, and Industry View (see queries.home_mix).
    conn = db.connect()
    try:
        articles = queries.home_mix(conn)
        ctx = {
            "request": request,
            "groups": queries.group_clusters(articles),
            "srcmap": queries.source_name_map(conn),
            "stats": queries.stats(conn),
            "top_headlines": queries.top_headlines(conn, limit=8),
            "voices": queries.voices_latest(conn, limit=6),
            "featured_blogs": queries.featured_voice_feed(conn, limit=5),
            "filters": None,
            "heading": None,
            "sub": None,
            "chips": None,
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/tech", response_class=HTMLResponse)
async def tech_view(request: Request):
    # General technology (not restricted to AI). The composite ranking scores
    # "significance to the AI field", which would bury general tech — so this
    # tab is newest-first.
    f = queries.FeedFilters(
        exclude_categories=("market", "blog", "podcast", "architecture", "industry"),
        sort="newest")
    ctx = _feed_context(
        request, f,
        heading="Tech News",
        sub="the latest technology news across major outlets, de-duplicated",
    )
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


@router.get("/blogs", response_class=HTMLResponse)
async def blogs_view(request: Request):
    # Blogs: trusted-voice essays only — newest first in the feed, top-ranked
    # blog posts (not news headlines) in the sidebar.
    conn = db.connect()
    try:
        articles = queries.voices_feed(conn)
        ctx = {
            "request": request,
            "groups": queries.group_clusters(articles),
            "srcmap": queries.source_name_map(conn),
            "stats": queries.stats(conn),
            "top_headlines": queries.top_voices(conn, limit=8),
            "headlines_title": "Top Blogs",
            "voices": None,
            "filters": None,
            "heading": "Blogs",
            "sub": "essays & analysis from trusted industry voices, newest first",
            "chips": None,
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/industry", response_class=HTMLResponse)
async def industry_view(request: Request):
    # Industry View: AI insights & white papers from consulting firms, analysts,
    # and research institutions; newest first, industry-only sidebar.
    conn = db.connect()
    try:
        articles = queries.industry_feed(conn)
        ctx = {
            "request": request,
            "groups": queries.group_clusters(articles),
            "srcmap": queries.source_name_map(conn),
            "stats": queries.stats(conn),
            "top_headlines": queries.top_industry(conn, limit=8),
            "headlines_title": "Top Insights",
            "voices": None,
            "filters": None,
            "heading": "Industry View",
            "sub": "AI insights & white papers from consulting firms, analysts & research institutions",
            "chips": None,
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/architecture", response_class=HTMLResponse)
async def architecture_view(request: Request):
    # Architecture: reference architectures & engineering deep-dives from
    # trustworthy publications (GitHub-routed items filtered out), newest first.
    conn = db.connect()
    try:
        articles = queries.architecture_feed(conn)
        ctx = {
            "request": request,
            "groups": queries.group_clusters(articles),
            "srcmap": queries.source_name_map(conn),
            "stats": queries.stats(conn),
            "top_headlines": queries.top_architecture(conn, limit=8),
            "headlines_title": "Top References",
            "voices": None,
            "filters": None,
            "heading": "Architecture",
            "sub": "reference architectures & engineering deep-dives from trusted sources, newest first",
            "chips": None,
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/podcasts", response_class=HTMLResponse)
async def podcasts_view(request: Request):
    # Podcasts: latest episodes from leading AI shows, newest first, with the
    # top-ranked episodes (not news headlines) in the sidebar.
    conn = db.connect()
    try:
        articles = queries.podcasts_feed(conn)
        ctx = {
            "request": request,
            "groups": queries.group_clusters(articles),
            "srcmap": queries.source_name_map(conn),
            "stats": queries.stats(conn),
            "top_headlines": queries.top_podcasts(conn, limit=8),
            "headlines_title": "Top Episodes",
            "voices": None,
            "filters": None,
            "heading": "Podcasts",
            "sub": "latest episodes from leading AI shows, newest first",
            "chips": None,
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/ticker")
async def ticker_quotes():
    """Quotes for the Nasdaq AI stock ticker bar (cached ~5 min)."""
    return await tickermod.quotes()


@router.get("/article/{article_id}/summary")
async def article_summary(article_id: int):
    """Reader-modal summary: return the cached ~50-120 word summary, generating
    it via the local LLM on first request. Falls back to the stored short
    summary / RSS text when Ollama is unavailable."""
    conn = db.connect()
    try:
        row = db.get_article_row(conn, article_id)
        if row is None:
            return {"id": article_id, "summary": ""}
        if row["detail_summary"]:
            return {"id": article_id, "summary": row["detail_summary"]}
        text = await summarize_mod.detail_summary(
            row["title"], row["summary"], row["raw_summary"] or ""
        )
        if text:
            db.save_detail_summary(conn, article_id, text)
            return {"id": article_id, "summary": text}
        fallback = row["summary"] or (row["raw_summary"] or "")[:400]
        return {"id": article_id, "summary": fallback}
    finally:
        conn.close()


@router.get("/market", response_class=HTMLResponse)
async def market_view(request: Request):
    # AI News: same layout as Home / Tech News, restricted to the market feeds
    # (the business side of AI — no research/launch coverage).
    f = queries.FeedFilters(category="market", sort="importance")
    ctx = _feed_context(request, f)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/market/{slug}", response_class=HTMLResponse)
async def market_category_view(request: Request, slug: str):
    cat = marketmod.BY_SLUG.get(slug)
    conn = db.connect()
    try:
        articles = queries.market_feed(conn, slug) if cat else []
        groups = queries.group_clusters(articles)
        srcmap = queries.source_name_map(conn)
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "market_category.html",
        {"cat": cat, "groups": groups, "srcmap": srcmap, "count": len(articles)},
    )
