"""Parse raw feed bytes into normalized Article objects."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from time import mktime

import feedparser

from ..config import settings
from ..models import Article, now_iso

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


def content_hash(title: str, url: str) -> str:
    """Stable hash for dedup. Title-normalized so the same story across feeds
    that share a canonical URL collides; otherwise unique per (title,url)."""
    norm = _WS_RE.sub(" ", title.lower()).strip()
    return hashlib.sha256(f"{norm}|{url}".encode()).hexdigest()


def _published_iso(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None) or entry.get(key)
        if val:
            try:
                return datetime.fromtimestamp(mktime(val), tz=timezone.utc).isoformat()
            except (ValueError, OverflowError):
                continue
    return None


def _matches_keywords(title: str, summary: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = f"{title} {summary}".lower()
    return any(k in hay for k in keywords)


def parse_feed(body: bytes, source_id: int, *, is_community: bool = False,
               keywords: list[str] | None = None) -> list[Article]:
    parsed = feedparser.parse(body)
    out: list[Article] = []
    for entry in parsed.entries[: settings.max_items_per_feed]:
        title = strip_html(entry.get("title", "")).strip()
        url = (entry.get("link") or "").strip()
        if not title or not url:
            continue
        raw_summary = strip_html(entry.get("summary") or entry.get("description") or "")
        if is_community and not _matches_keywords(title, raw_summary, keywords or []):
            continue
        guid = (entry.get("id") or entry.get("guid") or url).strip()
        author = entry.get("author") or None
        out.append(
            Article(
                source_id=source_id,
                guid=guid,
                url=url,
                title=title,
                author=author,
                published_at=_published_iso(entry),
                fetched_at=now_iso(),
                raw_summary=raw_summary[:2000],
                content_hash=content_hash(title, url),
            )
        )
    return out
