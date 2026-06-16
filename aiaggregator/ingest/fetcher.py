"""Async feed fetching with conditional GET (ETag / Last-Modified)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import settings


@dataclass
class FetchResult:
    status: str            # ok | not_modified | error: <msg>
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None


async def fetch_feed(client: httpx.AsyncClient, url: str, *, etag: str | None = None,
                     last_modified: str | None = None) -> FetchResult:
    headers: dict[str, str] = {"User-Agent": settings.user_agent}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        resp = await client.get(url, headers=headers, follow_redirects=True,
                                timeout=settings.http_timeout)
    except httpx.HTTPError as exc:
        return FetchResult(status=f"error: {type(exc).__name__}: {exc}")

    if resp.status_code == 304:
        return FetchResult(status="not_modified", etag=etag, last_modified=last_modified)
    if resp.status_code >= 400:
        return FetchResult(status=f"error: HTTP {resp.status_code}")

    return FetchResult(
        status="ok",
        body=resp.content,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
    )
