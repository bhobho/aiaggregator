"""SEO endpoints: robots.txt and sitemap.xml.

The sitemap lists only pages we actually allow into the index — the home page,
the section pages, and the owner's own posts. Aggregated third-party article
pages are served with `noindex, follow` (see routes.dashboard.post_view), so
they are deliberately absent here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from .. import db, queries
from ..config import settings

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
