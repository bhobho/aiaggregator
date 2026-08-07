"""SEO endpoints: robots.txt, sitemap.xml, and our own feed.xml.

The sitemap lists only pages we actually allow into the index — the home page,
the section pages, topic pages, and the owner's own posts. Aggregated
third-party article pages are served with `noindex, follow` (see
routes.dashboard.post_view), so they are deliberately absent here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from .. import db, queries
from ..config import settings
from ..enrich.summarize import TAG_VOCAB

router = APIRouter()

# Indexable navigational pages, with their crawl priority.
SECTIONS: list[tuple[str, str]] = [
    ("/", "1.0"),
    ("/market", "0.8"),
    ("/tech", "0.8"),
    ("/industry", "0.7"),
    ("/architecture", "0.7"),
    ("/blogs", "0.7"),
    ("/podcasts", "0.6"),
    ("/videos", "0.6"),
]


def base_url(request: Request) -> str:
    """Absolute site root: the configured public_url, else the request host."""
    return (settings.public_url.rstrip("/") if settings.public_url
            else str(request.base_url).rstrip("/"))


@router.get("/robots.txt", include_in_schema=False)
async def robots(request: Request) -> PlainTextResponse:
    base = base_url(request)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Disallow: {settings.analytics_path}\n"
        "\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body, media_type="text/plain")


def _lastmod(article) -> str:
    ts = article.published_at or article.fetched_at or ""
    return ts[:10] if len(ts) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request) -> Response:
    base = base_url(request)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows: list[tuple[str, str, str, str]] = [
        (f"{base}{path}", today, "daily", pri) for path, pri in SECTIONS
    ]
    rows += [(f"{base}/topic/{tag}", today, "daily", "0.6") for tag in TAG_VOCAB]
    conn = db.connect()
    try:
        for a in queries.my_posts_feed(conn, limit=500):
            rows.append((f"{base}/post/{a.id}", _lastmod(a), "monthly", "0.9"))
    finally:
        conn.close()

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq, pri in rows:
        parts.append(
            f"  <url><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{pri}</priority></url>"
        )
    parts.append("</urlset>")
    return Response("\n".join(parts), media_type="application/xml")


def _parse_dt(ts: str | None) -> datetime:
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


@router.get("/feed.xml", include_in_schema=False)
async def feed_xml(request: Request) -> Response:
    """Our own RSS feed: the top-ranked AI stories, deduplicated, linking to
    their in-portal /post/{id} page (not the original source) so subscribers
    land on the site — the same "keep readers in-portal" policy as everywhere
    else. A free distribution channel into feed readers, Feedly, IFTTT, etc."""
    base = base_url(request)
    conn = db.connect()
    try:
        articles = queries.dedupe_stories(queries.ranked_enriched(conn, days=7, limit=40))
    finally:
        conn.close()

    items = []
    for a in articles:
        link = f"{base}/post/{a.id}"
        desc = escape(a.summary or (a.raw_summary or "")[:300])
        pub = format_datetime(_parse_dt(a.published_at or a.fetched_at))
        items.append(
            f"    <item>\n"
            f"      <title>{escape(a.title)}</title>\n"
            f"      <link>{escape(link)}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(link)}</guid>\n"
            f"      <description>{desc}</description>\n"
            f"      <pubDate>{pub}</pubDate>\n"
            f"    </item>"
        )

    channel_desc = escape(
        "AI & Agentic-AI news, ranked and de-duplicated — labs, research, "
        "and major tech outlets, summarized locally."
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        f"  <title>AI Aggregator — Top AI Stories</title>\n"
        f"  <link>{escape(base)}/</link>\n"
        f"  <description>{channel_desc}</description>\n"
        f"  <language>en-us</language>\n"
        f"  <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>\n"
        + "\n".join(items) +
        "\n</channel></rss>"
    )
    return Response(body, media_type="application/rss+xml")
