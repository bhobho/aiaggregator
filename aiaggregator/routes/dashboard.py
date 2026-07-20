"""Platform routes: the 8-tab AI Intelligence Platform IA.

Briefing (dashboard) · Trends · Technology · Innovation · Business · Blogs ·
Podcasts · Resources — all layered on the existing ingest/enrich/rank pipeline.
Each article is decorated with heuristic technical/business impact scores
(see scoring.py) before it reaches a template.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, queries, resources, scoring, ticker as tickermod, trends as trendmod
from ..enrich import perspectives as persp_mod
from ..enrich import summarize as summarize_mod

router = APIRouter()

# Categories that belong to the "Technology" lens (everything AI-technical).
TECH_EXCLUDE = ("market", "industry", "blog", "podcast")
# Tags that mark an item as innovation / commercialization signal.
INNOVATION_TAGS = {"product", "funding", "open-source"}


# ---- decoration -------------------------------------------------------------

def _cards(conn, groups: list[dict]) -> list[dict]:
    """Turn clustered story groups into render-ready cards with impact scores."""
    srcmap = queries.source_name_map(conn)
    sizes = db.cluster_sizes(conn)
    out: list[dict] = []
    for g in groups:
        a = g["lead"]
        name, cat = srcmap.get(a.source_id, ("Source", "news"))
        csize = sizes.get(a.cluster_id, 1) if a.cluster_id else 1
        extras = [{
            "title": e.title,
            "url": e.url,
            "source": srcmap.get(e.source_id, ("Source", ""))[0],
        } for e in g["extras"]]
        out.append({
            "a": a,
            "extras": extras,
            "src": name,
            "category": cat,
            "score": scoring.score_article(a, cat, csize),
        })
    return out


def _cards_from(conn, articles: list) -> list[dict]:
    return _cards(conn, queries.group_clusters(articles))


# ---- per-tab article fetchers (reuse queries.*) -----------------------------

def _ranked_pool(conn, days: int = 21, limit: int = 140) -> list:
    f = queries.FeedFilters(sort="importance", days=days, limit=limit)
    return queries.dedupe_stories(queries.feed(conn, f))


def _technology(conn, limit: int = 60) -> list:
    f = queries.FeedFilters(exclude_categories=TECH_EXCLUDE, sort="importance",
                            days=21, limit=limit)
    return queries.dedupe_stories(queries.feed(conn, f))


def _business(conn, limit: int = 60) -> list:
    market = queries.feed(conn, queries.FeedFilters(category="market", sort="importance",
                                                    days=30, limit=limit))
    industry = queries.industry_feed(conn, limit=limit)
    merged = queries.rank_articles(conn, market + industry)
    return queries.dedupe_stories(merged)[:limit]


def _innovation(conn, limit: int = 60) -> list:
    pool = _ranked_pool(conn, days=30, limit=200)
    kw = ("startup", "raises", "raise ", "series ", "funding", "launch", "unveil",
          "introduc", "partnership", "acqui")
    picks = [a for a in pool
             if (set(a.tags) & INNOVATION_TAGS)
             or any(k in (a.title or "").lower() for k in kw)]
    return picks[:limit]


# ---- shared feed render -----------------------------------------------------

def _render_feed(request: Request, *, cards: list[dict], heading: str, sub: str,
                 icon: str, sidebar_title: str | None = None,
                 sidebar: list[dict] | None = None) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "feed.html", {
        "request": request,
        "cards": cards,
        "heading": heading,
        "sub": sub,
        "icon": icon,
        "sidebar_title": sidebar_title,
        "sidebar": sidebar,
    })


# ---- 1. Briefing (dashboard home) -------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def briefing(request: Request):
    conn = db.connect()
    try:
        pool = _ranked_pool(conn, days=21, limit=140)
        exec_cards = _cards_from(conn, pool[:5])
        trending = _cards_from(conn, pool[5:14])
        tech = _cards_from(conn, _technology(conn, limit=6))
        blogs = _cards_from(conn, queries.voices_feed(conn, limit=4))
        podcasts = _cards_from(conn, queries.podcasts_feed(conn, limit=4))
        radar = trendmod.radar(conn)
        ctx = {
            "request": request,
            "exec_cards": exec_cards,
            "trending": trending,
            "tech": tech,
            "blogs": blogs,
            "podcasts": podcasts,
            "radar": radar,
            "paths": resources.LEARNING_PATHS,
            "stats": queries.stats(conn),
        }
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "briefing.html", ctx)


# ---- 2. Trends --------------------------------------------------------------

@router.get("/trends", response_class=HTMLResponse)
async def trends_view(request: Request):
    conn = db.connect()
    try:
        data = trendmod.compute_trends(conn)
        srcmap = queries.source_name_map(conn)
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "trends.html",
                                      {"request": request, "trends": data, "srcmap": srcmap})


# ---- 3. Technology ----------------------------------------------------------

@router.get("/technology", response_class=HTMLResponse)
async def technology_view(request: Request):
    conn = db.connect()
    try:
        cards = _cards_from(conn, _technology(conn, limit=60))
    finally:
        conn.close()
    return _render_feed(
        request, cards=cards, icon="💻", heading="Technology",
        sub="Models, frameworks, research, and infrastructure — the technical frontier.",
    )


# ---- 4. Innovation ----------------------------------------------------------

@router.get("/innovation", response_class=HTMLResponse)
async def innovation_view(request: Request):
    conn = db.connect()
    try:
        cards = _cards_from(conn, _innovation(conn, limit=60))
    finally:
        conn.close()
    return _render_feed(
        request, cards=cards, icon="🚀", heading="Innovation",
        sub="Products, startups, funding, and use cases — where AI is creating value.",
    )


# ---- 5. Business ------------------------------------------------------------

@router.get("/business", response_class=HTMLResponse)
async def business_view(request: Request):
    conn = db.connect()
    try:
        cards = _cards_from(conn, _business(conn, limit=60))
    finally:
        conn.close()
    return _render_feed(
        request, cards=cards, icon="🏢", heading="Business",
        sub="Strategy, enterprise adoption, governance, and competitive intelligence.",
    )


# ---- 6. Blogs ---------------------------------------------------------------

@router.get("/blogs", response_class=HTMLResponse)
async def blogs_view(request: Request):
    conn = db.connect()
    try:
        cards = _cards_from(conn, queries.voices_feed(conn, limit=60))
        top = _cards_from(conn, queries.top_voices(conn, limit=6))
    finally:
        conn.close()
    return _render_feed(
        request, cards=cards, icon="📝", heading="Blogs",
        sub="Long-form analysis and engineering deep dives from trusted voices.",
        sidebar_title="Top Reads", sidebar=top,
    )


# ---- 7. Podcasts ------------------------------------------------------------

@router.get("/podcasts", response_class=HTMLResponse)
async def podcasts_view(request: Request):
    conn = db.connect()
    try:
        cards = _cards_from(conn, queries.podcasts_feed(conn, limit=60))
        top = _cards_from(conn, queries.top_podcasts(conn, limit=6))
    finally:
        conn.close()
    return _render_feed(
        request, cards=cards, icon="🎙", heading="Podcasts",
        sub="Latest episodes from leading AI shows — interviews, research, and founders.",
        sidebar_title="Top Episodes", sidebar=top,
    )


# ---- 8. Resources -----------------------------------------------------------

@router.get("/resources", response_class=HTMLResponse)
async def resources_view(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "resources.html", {
        "request": request,
        "paths": resources.LEARNING_PATHS,
        "resources": resources.RESOURCES,
        "glossary": resources.GLOSSARY,
    })


# ---- search / htmx partial --------------------------------------------------

@router.get("/feed", response_class=HTMLResponse)
async def feed_partial(request: Request, search: str = "", company: str = "",
                       category: str = "", days: str = "", min_importance: str = "",
                       sort: str = "importance"):
    """HTMX partial: a searched/filtered grid of cards (powers global search)."""
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    f = queries.FeedFilters(search=search or None, company=company or None,
                            category=category or None, days=_int(days),
                            min_importance=_int(min_importance), sort=sort or "importance")
    conn = db.connect()
    try:
        cards = _cards_from(conn, queries.dedupe_stories(queries.feed(conn, f)))
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "_list.html",
                                      {"request": request, "cards": cards})


# ---- reader modal data ------------------------------------------------------

@router.get("/article/{article_id}/perspectives")
async def article_perspectives(article_id: int):
    """Return the 5-perspective reader payload, generating + caching on first open.
    Falls back to templated views when Ollama is unavailable."""
    conn = db.connect()
    try:
        row = db.get_article_row(conn, article_id)
        if row is None:
            return {"id": article_id, "perspectives": {}}
        if row["perspectives"]:
            try:
                return {"id": article_id, "perspectives": json.loads(row["perspectives"])}
            except json.JSONDecodeError:
                pass
        data = await persp_mod.perspectives(
            row["title"], row["summary"], row["raw_summary"] or ""
        )
        db.save_perspectives(conn, article_id, json.dumps(data))
        return {"id": article_id, "perspectives": data}
    finally:
        conn.close()


@router.get("/article/{article_id}/summary")
async def article_summary(article_id: int):
    """Legacy reader summary (kept for compatibility)."""
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
        return {"id": article_id, "summary": row["summary"] or (row["raw_summary"] or "")[:400]}
    finally:
        conn.close()


@router.get("/ticker")
async def ticker_quotes():
    """Quotes for the market-signals ticker bar (cached ~5 min)."""
    return await tickermod.quotes()


# ---- retired-route redirects (keep old bookmarks alive) ---------------------

_REDIRECTS = {
    "/market": "/business",
    "/tech": "/technology",
    "/industry": "/business",
    "/architecture": "/technology",
    "/enterprises": "/business",
}


@router.get("/market", include_in_schema=False)
@router.get("/tech", include_in_schema=False)
@router.get("/industry", include_in_schema=False)
@router.get("/architecture", include_in_schema=False)
@router.get("/enterprises", include_in_schema=False)
async def _retired(request: Request):
    return RedirectResponse(_REDIRECTS.get(request.url.path, "/"), status_code=307)
