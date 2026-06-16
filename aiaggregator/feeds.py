"""Load the feed source list from feeds.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import settings
from .models import Source


def load_feeds(path: Path | None = None) -> tuple[list[Source], list[str]]:
    """Return (sources, community_keywords)."""
    p = path or settings.feeds_path
    data = yaml.safe_load(p.read_text())
    sources = [
        Source(
            name=s["name"],
            url=s["url"],
            category=s["category"],
            company=s.get("company"),
        )
        for s in data.get("sources", [])
    ]
    keywords = [k.lower() for k in data.get("community_keywords", [])]
    return sources, keywords
