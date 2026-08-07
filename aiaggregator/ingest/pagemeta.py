"""Fallback lead image: scrape og:image/twitter:image off the article's own page.

Most of our feeds are enclosure/media-poor, so `_entry_image` in normalize.py
comes back empty for the large majority of items (feed items simply don't carry
an image). Once we have the article's real URL, though, virtually every
publisher sets an Open Graph or Twitter Card image meta tag in <head> for link
previews — so a lightweight GET + regex scan recovers a thumbnail for most of
the rest.

A large share of our "AI News" sources are Google News queries (see
feeds.yaml), whose article links are opaque `news.google.com/rss/articles/...`
redirects rather than the publisher's actual URL — fetching that wrapper page
directly finds no og:image (it's not the real article). `googlenews.resolve`
unwraps those first so the scrape below actually lands on the publisher page.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

from ..config import settings
from . import googlenews

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_PROP_RE = re.compile(r"""(?:property|name)\s*=\s*["']([^"']+)["']""", re.I)
_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']*)["']""", re.I)
_HEAD_CLOSE_RE = re.compile(r"</head", re.I)

_IMG_PROPS = {
    "og:image", "og:image:url", "og:image:secure_url",
    "twitter:image", "twitter:image:src",
}

# Meta tags live in <head>; cap how much of the page we read so one slow/huge
# article page can't stall a whole backfill pass.
_MAX_BYTES = 300_000


def _extract_og_image(html: str, base_url: str) -> str | None:
    m = _HEAD_CLOSE_RE.search(html)
    head = html[: m.start()] if m else html
    for tag in _META_TAG_RE.findall(head):
        prop = _PROP_RE.search(tag)
        if not prop or prop.group(1).strip().lower() not in _IMG_PROPS:
            continue
        content = _CONTENT_RE.search(tag)
        url = (content.group(1).strip() if content else "")
        if url:
            return urljoin(base_url, url)
    return None


async def fetch_lead_image(client: httpx.AsyncClient, url: str) -> str | None:
    """Best-effort og:image/twitter:image for an article URL. None on any failure
    (dead link, non-HTML response, no such tag, timeout, ...) — callers should
    treat that as "checked, nothing found" rather than an error."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    resolved = await googlenews.resolve(client, url)
    target = resolved or url
    try:
        async with client.stream(
            "GET", target, headers={"User-Agent": settings.user_agent},
            follow_redirects=True, timeout=settings.http_timeout,
        ) as resp:
            if resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype.lower():
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= _MAX_BYTES or b"</head" in chunk.lower():
                    break
            html = b"".join(chunks).decode("utf-8", errors="ignore")
            final_url = str(resp.url)
    except (httpx.HTTPError, UnicodeDecodeError):
        return None
    return _extract_og_image(html, final_url)
