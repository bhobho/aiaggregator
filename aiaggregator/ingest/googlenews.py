"""Resolve a Google News RSS redirect link to the real publisher URL.

Roughly a third of our AI News sources are Google News site/topic queries
(no direct RSS from the publisher — see feeds.yaml), which is the whole point:
it's a free way to cover outlets without their own feed. The cost is that
every `<link>` Google News hands back is an opaque redirect
(`news.google.com/rss/articles/<id>`), not the real article URL — so our
og:image scraper (pagemeta.py) was fetching Google's wrapper page and finding
nothing there to scrape, not the publisher's page with the actual thumbnail.

Google encodes the real URL into that `<id>` in one of two ways:
  - pre-mid-2024 links: the URL is embedded directly in the base64 payload
    (a small protobuf-ish byte structure) — decodable fully offline.
  - links since then: the payload is just an opaque token ("AU_yqL...") and
    the real URL has to be looked up via Google News' own (undocumented, but
    stable and widely relied upon — see the actively-maintained
    `googlenewsdecoder` PyPI package) internal batchexecute endpoint.

Both paths are implemented here so old and new-style links both resolve;
in practice nearly everything we see today is the new style.
"""
from __future__ import annotations

import base64
import binascii
from urllib.parse import quote, urlsplit

import httpx

from ..config import settings

_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je"


def article_id(url: str) -> str | None:
    """The opaque id out of a news.google.com/rss/articles/<id> (or /read/<id>)
    link, or None if `url` isn't a Google News redirect link at all."""
    parsed = urlsplit(url)
    if parsed.netloc != "news.google.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[-2] not in ("articles", "read"):
        return None
    return parts[-1]


def _decode_offline(aid: str) -> str | None:
    """Old-style (pre-mid-2024) encoding: the real URL's bytes are embedded
    directly in the base64 payload. Returns None for the newer opaque-token
    style, which needs `_decode_online` instead."""
    try:
        raw = base64.urlsafe_b64decode(aid + "=" * (-len(aid) % 4))
    except (ValueError, binascii.Error):
        return None
    prefix, suffix = bytes((0x08, 0x13, 0x22)), bytes((0xD2, 0x01, 0x00))
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    if raw.endswith(suffix):
        raw = raw[: -len(suffix)]
    if not raw:
        return None
    length = raw[0]
    body = raw[2: 2 + length] if length >= 0x80 else raw[1: 1 + length]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or text.startswith("AU_yqL"):
        return None  # opaque new-style token, not a real URL
    return text if text.startswith(("http://", "https://")) else None


def _batchexecute_body(aid: str) -> str:
    # Reverse-engineered RPC envelope Google News' own web UI uses to resolve
    # a card's opaque id back to the source URL ("Fbv4je" / "garturlreq").
    payload = (
        '[[["Fbv4je","[\\"garturlreq\\",[[\\"en-US\\",\\"US\\",[\\"FINANCE_TOP_INDICES\\",'
        '\\"WEB_TEST_1_0_0\\"],null,null,1,1,\\"US:en\\",null,180,null,null,null,null,null,0,'
        'null,null,[1608992183,723341000]],\\"en-US\\",\\"US\\",1,[2,3,4,8],1,0,\\"655000234\\",'
        '0,0,null,0],\\"' + aid + '\\"]",null,"generic"]]]'
    )
    return "f.req=" + quote(payload, safe="")


async def _decode_online(client: httpx.AsyncClient, aid: str) -> str | None:
    try:
        resp = await client.post(
            _BATCHEXECUTE_URL,
            content=_batchexecute_body(aid),
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                "Referer": "https://news.google.com/",
                "User-Agent": settings.user_agent,
            },
            timeout=settings.http_timeout,
        )
        if resp.status_code >= 400:
            return None
        text = resp.text
    except httpx.HTTPError:
        return None
    header = '["garturlres","'
    start = text.find(header)
    if start == -1:
        return None
    start += len(header)
    end = text.find('",', start)
    if end == -1:
        return None
    url = text[start:end]
    return url if url.startswith(("http://", "https://")) else None


async def resolve(client: httpx.AsyncClient, url: str) -> str | None:
    """The real publisher URL behind a Google News redirect link, or None if
    `url` isn't one (or resolution fails — dead link, endpoint hiccup, format
    changed again). Tries the free offline decode first, only falls back to
    the network round-trip when the link is the newer opaque-token style."""
    aid = article_id(url)
    if not aid:
        return None
    return _decode_offline(aid) or await _decode_online(client, aid)
