"""Domain dataclasses shared across the app."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Source:
    name: str
    url: str
    category: str  # lab | research | news | market | community
    company: str | None = None
    id: int | None = None
    active: bool = True
    etag: str | None = None
    last_modified: str | None = None
    last_fetch: str | None = None
    last_status: str | None = None  # ok | error: ...
    item_count: int = 0


@dataclass
class Article:
    source_id: int
    guid: str
    url: str
    title: str
    content_hash: str
    id: int | None = None
    author: str | None = None
    published_at: str | None = None  # ISO 8601
    fetched_at: str | None = None
    raw_summary: str = ""
    # enrichment
    status: str = "new"  # new | enriched | failed
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    importance: int | None = None
    cluster_id: int | None = None

    @staticmethod
    def from_row(row) -> "Article":
        return Article(
            id=row["id"],
            source_id=row["source_id"],
            guid=row["guid"],
            url=row["url"],
            title=row["title"],
            content_hash=row["content_hash"],
            author=row["author"],
            published_at=row["published_at"],
            fetched_at=row["fetched_at"],
            raw_summary=row["raw_summary"] or "",
            status=row["status"],
            summary=row["summary"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            companies=json.loads(row["companies"]) if row["companies"] else [],
            importance=row["importance"],
            cluster_id=row["cluster_id"],
        )


@dataclass
class Cluster:
    id: int
    label: str
    top_article_id: int
    size: int
    created_at: str


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()
