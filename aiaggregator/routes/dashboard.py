"""Dashboard routes: feed and filtered/searched partials."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import (db, market as marketmod, queries, sanitize, ticker as tickermod,
                vendors as vendormod)
from ..config import settings
from ..enrich import summarize as summarize_mod
from ..models import Article

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
                  chips: list | None = None, desc: str | None = None) -> dict:
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
            "og_desc": desc,   # unique meta description per section
        }
    finally:
        conn.close()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # My Page: the site owner's own posts — Medium (and LinkedIn, once a feed is
    # configured) — with thumbnail images.
    conn = db.connect()
    try:
        ctx = {
            "request": request,
            "posts_groups": queries.group_clusters(queries.my_posts_feed(conn, limit=60)),
            "og_desc": "Articles and posts by Neeraj Pandey on AI, agents and "
                       "architecture — practitioner notes on building AI systems.",
            "srcmap": queries.source_name_map(conn),
            "stats": queries.stats(conn),
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "mypage.html", ctx)


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
        desc="The latest technology news across major outlets, de-duplicated and "
             "summarized — models, tools, research and industry moves.",
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
            "og_desc": "Essays and analysis on AI from trusted industry voices — "
                       "engineering blogs, research write-ups and practitioner deep dives.",
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
            "og_desc": "AI insights and white papers from consulting firms, analysts "
                       "and research institutions — adoption, strategy and market outlook.",
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
            "og_desc": "Reference architectures and engineering deep dives for building "
                       "AI systems — RAG, agents, inference and platform design.",
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
            "og_desc": "Latest episodes from leading AI podcasts — interviews, research "
                       "discussions and founder conversations, newest first.",
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


@router.get("/post/{article_id}", response_class=HTMLResponse)
async def post_view(request: Request, article_id: int):
    """In-portal article page. The owner's own posts (the pinned Medium source)
    render in full from the sanitized feed content; everything else shows the
    summary with a link out (we don't republish other outlets' full text)."""
    conn = db.connect()
    try:
        row = db.get_article_row(conn, article_id)
        if row is None:
            return PlainTextResponse("Not found", status_code=404)
        article = Article.from_row(row)
        srcmap = queries.source_name_map(conn)
        srcname = srcmap.get(article.source_id, ("Source", "news"))[0]
        is_own = srcname in queries.MY_SOURCES

        # Render whatever the publisher syndicated in their own feed
        # (content:encoded), sanitized. Feeds that only carry a summary fall back
        # to the short version plus a link to the original.
        full_html = sanitize.clean(article.content) if article.content else ""
        summary = article.summary or (article.raw_summary or "")[:600]
        # Many feeds repeat the cover image as the first image of the body — only
        # show the standalone hero when the body doesn't already contain it.
        show_hero = bool(article.image_url) and article.image_url not in full_html

        base = settings.public_url.rstrip("/") if settings.public_url \
            else str(request.base_url).rstrip("/")
        post_url = f"{base}/post/{article.id}"

        # Only our own writing goes into the search index; syndicated third-party
        # articles are crawlable (links followed) but not indexed, so the site
        # isn't judged as republished/duplicate content.
        robots_meta = "" if is_own else "noindex, follow"
        jsonld = json.dumps({
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": article.title[:110],
            "description": (summary or article.title)[:200],
            "image": [article.image_url] if article.image_url else [],
            "datePublished": article.published_at or article.fetched_at,
            "dateModified": article.published_at or article.fetched_at,
            "author": {"@type": "Person", "name": "Neeraj Pandey"},
            "publisher": {
                "@type": "Organization",
                "name": "AI Aggregator",
                "logo": {"@type": "ImageObject", "url": f"{base}/static/og-card.png"},
            },
            "mainEntityOfPage": {"@type": "WebPage", "@id": post_url},
            "url": post_url,
        }, ensure_ascii=False) if is_own else ""
        ctx = {
            "request": request,
            "article": article,
            "srcname": srcname,
            "is_own": is_own,
            "full_html": full_html,
            "summary": summary,
            "show_hero": show_hero,
            "base": base,
            "post_url": post_url,
            # per-article link-preview metadata (see base.html)
            "og_title": article.title,
            "og_desc": (summary or article.title)[:200],
            "og_image": article.image_url or "",
            "og_type": "article",
            "canonical": post_url,
            "robots_meta": robots_meta,
            "jsonld": jsonld,
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "post.html", ctx)


@router.get("/market", response_class=HTMLResponse)
async def market_view(request: Request):
    # AI News: same layout as Home / Tech News, restricted to the market feeds
    # (the business side of AI — no research/launch coverage).
    f = queries.FeedFilters(category="market", sort="importance")
    ctx = _feed_context(
        request, f,
        heading="AI News",
        sub="the business of AI — funding, launches, policy and market moves",
        desc="AI news: funding, model launches, policy and market moves across the "
             "AI industry, ranked and de-duplicated from public sources.",
    )
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
