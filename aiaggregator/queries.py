"""Read queries that power the dashboard (filters, search, grouping)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import db, market as marketmod, ranking, textnorm, vendors as vendormod
from .models import Article


@dataclass
class FeedFilters:
    company: str | None = None
    category: str | None = None
    exclude_categories: tuple[str, ...] | None = None  # drop sources of these categories
    days: int | None = None         # time window
    min_importance: int | None = None
    search: str | None = None
    sort: str = "newest"            # newest | importance
    limit: int = 100


def _fts_match(term: str) -> str:
    # Sanitize into a safe FTS5 prefix query.
    words = [w for w in "".join(c if c.isalnum() else " " for c in term).split() if w]
    return " ".join(f"{w}*" for w in words)


def feed(conn: sqlite3.Connection, f: FeedFilters) -> list[Article]:
    where = ["s.active = 1"]
    params: list = []

    if f.search:
        match = _fts_match(f.search)
        if match:
            where.append(
                "a.id IN (SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
            )
            params.append(match)

    if f.category:
        where.append("s.category = ?")
        params.append(f.category)
    if f.exclude_categories:
        where.append(f"s.category NOT IN ({','.join('?' * len(f.exclude_categories))})")
        params.extend(f.exclude_categories)
    if f.company:
        where.append("(s.company = ? OR a.companies LIKE ?)")
        params.extend([f.company, f'%"{f.company}"%'])
    if f.min_importance is not None:
        where.append("COALESCE(a.importance, 0) >= ?")
        params.append(f.min_importance)
    if f.days is not None:
        where.append("COALESCE(a.published_at, a.fetched_at) >= datetime('now', ?)")
        params.append(f"-{f.days} days")

    where_sql = " AND ".join(where)

    if f.sort == "importance":
        # Rank the whole (filtered) set by composite score in Python — the corpus is
        # small (~hundreds), so no candidate pre-filter. Pooling by importance OR
        # recency alone would drop relevant items (fresh-but-modest, or old-but-major)
        # before the composite ranking could weigh them fairly.
        sql = f"""
            SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE {where_sql}
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
            LIMIT ?
        """
        params.append(5000)
        rows = conn.execute(sql, params).fetchall()
        ranked = rank_articles(conn, [Article.from_row(r) for r in rows])
        return ranked[: f.limit]

    sql = f"""
        SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
        WHERE {where_sql}
        ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
        LIMIT ?
    """
    params.append(f.limit)
    rows = conn.execute(sql, params).fetchall()
    return [Article.from_row(r) for r in rows]


def rank_articles(conn: sqlite3.Connection, articles: list[Article]) -> list[Article]:
    """Sort articles by the composite rank score (see ranking.py)."""
    srcmap = source_name_map(conn)
    sizes = db.cluster_sizes(conn)
    now = datetime.now(timezone.utc)

    def key(a: Article) -> float:
        category = srcmap.get(a.source_id, ("", ""))[1]
        csize = sizes.get(a.cluster_id, 1) if a.cluster_id else 1
        return ranking.rank_score(a, category, csize, now)

    return sorted(articles, key=key, reverse=True)


def ranked_enriched(conn: sqlite3.Connection, days: int, limit: int) -> list[Article]:
    """Top enriched articles within a window, ordered by composite rank."""
    rows = conn.execute(
        """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE s.active = 1 AND a.status = 'enriched'
                 AND a.fetched_at >= datetime('now', ?)
           ORDER BY COALESCE(a.importance, 0) DESC, a.fetched_at DESC
           LIMIT ?""",
        (f"-{days} days", limit * 5),
    ).fetchall()
    return rank_articles(conn, [Article.from_row(r) for r in rows])[:limit]


def source_name_map(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    return {
        r["id"]: (r["name"], r["category"])
        for r in conn.execute("SELECT id, name, category FROM sources")
    }


def source_company_map(conn: sqlite3.Connection) -> dict[int, str | None]:
    return {r["id"]: r["company"] for r in conn.execute("SELECT id, company FROM sources")}


def _all_active(conn: sqlite3.Connection) -> list[Article]:
    rows = conn.execute(
        """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE s.active = 1
           ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT 5000"""
    ).fetchall()
    return [Article.from_row(r) for r in rows]


def vendor_tiles(conn: sqlite3.Connection, top: int = 2) -> list[dict]:
    """One bucket per curated vendor: count, today-count, and top-ranked headlines."""
    articles = _all_active(conn)
    companies = source_company_map(conn)
    today = date.today().isoformat()

    buckets: dict[str, list[Article]] = {v.slug: [] for v in vendormod.VENDORS}
    for a in articles:
        for v in vendormod.vendors_for(a, companies.get(a.source_id)):
            buckets[v.slug].append(a)

    tiles = []
    for v in vendormod.VENDORS:
        arts = buckets[v.slug]
        if not arts:
            continue
        ranked = rank_articles(conn, arts)
        # de-dupe tile previews: one entry per story
        top_list = unique_stories(ranked, top)
        today_n = sum(1 for a in arts if (a.published_at or a.fetched_at or "")[:10] == today)
        tiles.append({
            "vendor": v,
            "count": len(arts),
            "today": today_n,
            "top": top_list,
        })
    tiles.sort(key=lambda t: (t["today"], t["count"]), reverse=True)
    return tiles


def vendor_feed(conn: sqlite3.Connection, slug: str, limit: int = 80) -> list[Article]:
    v = vendormod.BY_SLUG.get(slug)
    if v is None:
        return []
    companies = source_company_map(conn)
    arts = [a for a in _all_active(conn)
            if any(vv.slug == slug for vv in vendormod.vendors_for(a, companies.get(a.source_id)))]
    return rank_articles(conn, arts)[:limit]


def dedupe_stories(articles: list[Article]) -> list[Article]:
    """Drop duplicates of the same story pulled in via different feeds: same URL
    or same normalized title (outlet suffix stripped, so 'Story - The Hill' and
    'Story - CBS News' collapse). Keeps the first (best-ranked) copy;
    near-duplicates with genuinely different titles are collapsed by clustering."""
    seen: set = set()
    out: list[Article] = []
    for a in articles:
        keys = {("u", a.url)}
        norm = textnorm.normalize_title(a.title)
        if norm:
            keys.add(("t", norm))
        if keys & seen:
            continue
        seen |= keys
        out.append(a)
    return out


def unique_stories(articles: list[Article], limit: int) -> list[Article]:
    """One entry per story: collapse by cluster AND normalized title."""
    seen: set = set()
    out: list[Article] = []
    for a in articles:
        keys = set()
        norm = textnorm.normalize_title(a.title)
        if norm:
            keys.add(("t", norm))
        if a.cluster_id is not None:
            keys.add(("c", a.cluster_id))
        if keys & seen:
            continue
        seen |= keys
        out.append(a)
        if len(out) >= limit:
            break
    return out


def market_feed(conn: sqlite3.Connection, slug: str, limit: int = 80) -> list[Article]:
    """Ranked, de-duplicated stories for one market category."""
    c = marketmod.BY_SLUG.get(slug)
    if c is None:
        return []
    srcmap = source_name_map(conn)
    arts = [
        a for a in _all_active(conn)
        if any(cc.slug == slug
               for cc in marketmod.categories_for(a, srcmap.get(a.source_id, ("", ""))[0]))
    ]
    return dedupe_stories(rank_articles(conn, arts))[:limit]


def group_clusters(articles: list[Article]) -> list[dict]:
    """Collapse clustered articles: lead article + extras. Singletons pass through."""
    groups: dict[int, dict] = {}
    out: list[dict] = []
    for a in articles:
        if a.cluster_id is None:
            out.append({"lead": a, "extras": []})
            continue
        g = groups.get(a.cluster_id)
        if g is None:
            # first occurrence is the best-ranked variant (input is rank-sorted) -> lead
            g = {"lead": a, "extras": []}
            groups[a.cluster_id] = g
            out.append(g)
        else:
            g["extras"].append(a)
    return out


# Blog sources (category `blog` in feeds.yaml). Their posts rank below
# same-day breaking news in the composite feed, so the sidebar surfaces each
# blog's latest post directly and /blogs lists them all.
# The featured author whose posts are pinned to the top of the Blogs tab.
PRIORITY_VOICE = "Neeraj Pandey (Medium)"
SECOND_VOICE = "Neeraj Pandey (Hashnode)"

VOICE_SOURCES = {
    PRIORITY_VOICE,
    SECOND_VOICE,
    "Hugging Face Blog",
    "Sebastian Raschka Blog",
    "Towards AI",
    "AssemblyAI Blog",
    "Pinecone Blog",
    "Weights & Biases Blog",
    "LangChain Blog",
    "LlamaIndex Blog",
    "Cohere Blog",
    "NVIDIA Developer Blog (AI)",
    "Microsoft AI Blog",
    "AWS Machine Learning Blog",
    "Google AI Blog",
    "Databricks Blog",
    "Mistral AI Blog",
}


def voices_latest(conn: sqlite3.Connection, limit: int = 6) -> list[Article]:
    """Most recent post per trusted-voice source, newest first."""
    marks = ",".join("?" * len(VOICE_SOURCES))
    rows = conn.execute(
        f"""SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE s.active = 1 AND s.name IN ({marks})
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT 200""",
        list(VOICE_SOURCES),
    ).fetchall()
    seen: set[int] = set()
    out: list[Article] = []
    for r in rows:
        a = Article.from_row(r)
        if a.source_id in seen:
            continue
        seen.add(a.source_id)
        out.append(a)
        if len(out) >= limit:
            break
    return out


# Podcast sources (category `podcast` in feeds.yaml), shown on /podcasts.
PODCAST_SOURCES = {
    "AI Daily Brief",
    "The Artificial Intelligence Show",
    "Practical AI",
    "TWIML AI Podcast",
    "Eye on AI",
    "Last Week in AI",
    "AI Today Podcast",
    "Me, Myself, and AI",
    "No Priors",
    "Latent Space Podcast",
    "Machine Learning Street Talk",
    "Hard Fork",
    "Lex Fridman Podcast",
    "The Cognitive Revolution",
    "NVIDIA AI Podcast",
}


# Architecture sources (category `architecture` in feeds.yaml), shown on
# /architecture: reference architectures & engineering deep-dives from
# trustworthy publications (not GitHub repo commit/release feeds).
ARCHITECTURE_SOURCES = {
    "AWS Architecture Blog",
    "AWS Machine Learning Blog",
    "Google Cloud AI Architecture",
    "NVIDIA Technical Blog",
    "Microsoft Engineering (ISE)",
    "Microsoft Semantic Kernel Blog",
    "Databricks Blog",
    "LangChain Blog",
    "LlamaIndex Blog",
    "Martin Fowler",
    "InfoQ AI, ML & Data Engineering",
    "InfoQ Architecture & Design",
    "The New Stack",
    "Netflix Tech Blog",
    "Meta Engineering",
}


def _named_sources_feed(conn: sqlite3.Connection, names: set[str],
                        limit: int) -> list[Article]:
    """Newest-first, de-duplicated items from the named sources (posts and
    episodes age better than news, so recency beats the composite ranking)."""
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"""SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE s.active = 1 AND s.name IN ({marks})
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?""",
        [*names, limit * 3],
    ).fetchall()
    return dedupe_stories([Article.from_row(r) for r in rows])[:limit]


def voices_feed(conn: sqlite3.Connection, limit: int = 80) -> list[Article]:
    # Pin the owner's own posts to the top, then the rest of the trusted voices
    # newest-first. Fetched separately so the owner's posts surface even when
    # they're older than the recent-window cutoff of the other blogs.
    featured = _named_sources_feed(conn, MY_SOURCES, limit)
    rest = _named_sources_feed(conn, VOICE_SOURCES - MY_SOURCES, limit)
    return dedupe_stories(featured + rest)[:limit]


def featured_voice_feed(conn: sqlite3.Connection, limit: int = 5) -> list[Article]:
    """The owner's latest blog posts — powers the Home 'Featured Blogs' panel."""
    return _named_sources_feed(conn, MY_SOURCES, limit)


# ---- "My Page": the site owner's own posts (Medium + Hashnode) --------------
MY_MEDIUM_SOURCE = PRIORITY_VOICE            # "Neeraj Pandey (Medium)"
MY_HASHNODE_SOURCE = SECOND_VOICE            # "Neeraj Pandey (Hashnode)"
MY_LINKEDIN_SOURCE = "Neeraj Pandey (LinkedIn)"

# Sources whose posts are the owner's own: shown on My Page and rendered in full
# in-portal (see routes.dashboard.post_view).
MY_SOURCES = {MY_MEDIUM_SOURCE, MY_HASHNODE_SOURCE}


def my_posts_feed(conn: sqlite3.Connection, limit: int = 60) -> list[Article]:
    """All of the owner's own posts (Medium + Hashnode), newest first."""
    return _named_sources_feed(conn, MY_SOURCES, limit)


def my_medium_feed(conn: sqlite3.Connection, limit: int = 60) -> list[Article]:
    """The owner's Medium posts, newest first (empty if none ingested yet)."""
    return _named_sources_feed(conn, {MY_MEDIUM_SOURCE}, limit)


def my_linkedin_feed(conn: sqlite3.Connection, limit: int = 60) -> list[Article]:
    """The owner's LinkedIn posts, newest first (empty until a LinkedIn feed is
    configured — LinkedIn has no public RSS, so this needs an RSS-bridge source)."""
    return _named_sources_feed(conn, {MY_LINKEDIN_SOURCE}, limit)


def _latest_by_categories(conn: sqlite3.Connection, cats: tuple[str, ...],
                          limit: int) -> list[Article]:
    marks = ",".join("?" * len(cats))
    rows = conn.execute(
        f"""SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE s.active = 1 AND s.category IN ({marks})
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?""",
        [*cats, limit],
    ).fetchall()
    return [Article.from_row(r) for r in rows]


def home_mix(conn: sqlite3.Connection, per_section: int = 30,
             limit: int = 90) -> list[Article]:
    """Home feed: a balanced blend of the latest from AI News, Tech News, Blogs,
    Architecture, and Industry View. Round-robin interleaves the sections so each
    is represented near the top, then de-duplicates across the whole set."""
    sections = [
        _latest_by_categories(conn, ("market",), per_section),                 # AI News
        _latest_by_categories(conn, ("news", "lab", "research", "community"),   # Tech News
                              per_section),
        _latest_by_categories(conn, ("blog",), per_section),                   # Blogs
        _latest_by_categories(conn, ("architecture",), per_section),           # Architecture
        industry_feed(conn, per_section),                                      # Industry View
    ]
    interleaved: list[Article] = []
    for i in range(max((len(s) for s in sections), default=0)):
        for s in sections:
            if i < len(s):
                interleaved.append(s[i])
    return dedupe_stories(interleaved)[:limit]


def podcasts_feed(conn: sqlite3.Connection, limit: int = 80) -> list[Article]:
    return _named_sources_feed(conn, PODCAST_SOURCES, limit)


def top_podcasts(conn: sqlite3.Connection, limit: int = 8) -> list[Article]:
    """Top episodes for the Podcasts-page sidebar, by composite rank."""
    arts = podcasts_feed(conn, limit=200)
    return unique_stories(rank_articles(conn, arts), limit)


# Industry View sources (category `industry` in feeds.yaml): consulting,
# analyst & research-institution AI insights and white papers.
# HBR, MIT Sloan Management Review, and CB Insights were dropped — their content
# sits behind a subscription. The remaining firms surface free coverage.
INDUSTRY_SOURCES = {
    "BCG Insights",
    "McKinsey QuantumBlack Insights",
    "Bain & Company Insights",
    "Deloitte AI Institute",
    "Accenture AI Insights",
    "Gartner Artificial Intelligence",
    "Forrester AI",
    "IDC Artificial Intelligence",
    "Stanford AI Index",
    "OpenAI Research",
    "Google DeepMind",
}


# Subscription/paywalled outlets to drop from Industry View — their articles
# require a login, so they aren't useful in an aggregator. Matched (substring,
# lowercased) against the publisher in a headline's " - Outlet" suffix.
PAYWALL_OUTLETS = {
    "bloomberg", "business insider", "insider.com", "seeking alpha", "fortune",
    "wall street journal", "wsj", "financial times", " ft.com", "the information",
    "barron", "the economist", "new york times", "nytimes", "nikkei", "forbes",
    "the times", "the telegraph", "financial post", "the atlantic", "foreign affairs",
    "statista", "the new yorker", "puck", "the wall street journal",
    "investing.com", "moneycontrol", "livemint", "the ken",
}


def _is_paywalled(a: Article) -> bool:
    outlet = textnorm.outlet_of(a.title).lower()
    return bool(outlet) and any(p in outlet for p in PAYWALL_OUTLETS)


def industry_feed(conn: sqlite3.Connection, limit: int = 80) -> list[Article]:
    # Drop subscription-only outlets; keep free, relevant insights.
    arts = _named_sources_feed(conn, INDUSTRY_SOURCES, limit * 3)
    arts = [a for a in arts if not _is_paywalled(a)]
    return arts[:limit]


def top_industry(conn: sqlite3.Connection, limit: int = 8) -> list[Article]:
    """Top items for the Industry-View-page sidebar, by composite rank."""
    arts = industry_feed(conn, limit=200)
    return unique_stories(rank_articles(conn, arts), limit)


def architecture_feed(conn: sqlite3.Connection, limit: int = 80) -> list[Article]:
    # Exclude items that just route to a GitHub repo — architecture posts should
    # be trustworthy articles, not raw commits/releases.
    arts = _named_sources_feed(conn, ARCHITECTURE_SOURCES, limit * 2)
    arts = [a for a in arts if "github.com" not in (a.url or "").lower()]
    return arts[:limit]


def top_architecture(conn: sqlite3.Connection, limit: int = 8) -> list[Article]:
    """Top items for the Architecture-page sidebar, by composite rank."""
    arts = architecture_feed(conn, limit=200)
    return unique_stories(rank_articles(conn, arts), limit)


def top_voices(conn: sqlite3.Connection, limit: int = 8) -> list[Article]:
    """Top blog posts for the Blogs-page sidebar: the featured author first, then
    the other trusted-voice posts by composite rank, one entry per story."""
    arts = voices_feed(conn, limit=200)
    srcmap = source_name_map(conn)
    featured = [a for a in arts if srcmap.get(a.source_id, ("", ""))[0] in MY_SOURCES]
    rest = [a for a in arts if srcmap.get(a.source_id, ("", ""))[0] not in MY_SOURCES]
    return unique_stories(featured + rank_articles(conn, rest), limit)


def top_headlines(conn: sqlite3.Connection, limit: int = 8) -> list[Article]:
    """Top headlines for the sidebar: enriched articles by composite rank,
    one entry per story (cluster/title de-duplicated), with recent items
    filling in if there aren't enough enriched yet."""
    rows = conn.execute(
        """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE s.active = 1 AND a.status = 'enriched'
           ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
           LIMIT 5000""",
    ).fetchall()
    ranked = rank_articles(conn, [Article.from_row(r) for r in rows])
    arts = unique_stories(ranked, limit)
    if len(arts) < limit:
        seen_ids = {a.id for a in arts}
        extra = conn.execute(
            """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE s.active = 1
               ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?""",
            (limit * 3,),
        ).fetchall()
        fill = [Article.from_row(r) for r in extra if r["id"] not in seen_ids]
        arts = unique_stories(arts + fill, limit)
    return arts


def companies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT company FROM sources WHERE company IS NOT NULL ORDER BY company"
    )
    return [r["company"] for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT
             COUNT(*) total,
             SUM(status='enriched') enriched,
             SUM(status='new') pending,
             SUM(status='failed') failed
           FROM articles"""
    ).fetchone()
    clusters = conn.execute("SELECT COUNT(*) c FROM clusters").fetchone()["c"]
    return {
        "total": row["total"] or 0,
        "enriched": row["enriched"] or 0,
        "pending": row["pending"] or 0,
        "failed": row["failed"] or 0,
        "clusters": clusters,
    }
