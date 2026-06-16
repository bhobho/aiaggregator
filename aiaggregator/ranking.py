"""Composite story ranking.

Blends four signals into a single 0..~1 score so "Top stories" reflects more than
the LLM's raw opinion:

    rank = w1*importance + w2*recency + w3*cluster_size + w4*source_trust

Each component is normalized to 0..1; weights come from settings and need not sum to 1.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import settings
from .models import Article

# Source-type trust: first-party labs > major outlets > community aggregators.
SOURCE_WEIGHT = {"lab": 1.0, "research": 0.9, "news": 0.75, "community": 0.5}


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def importance_score(article: Article) -> float:
    return max(0.0, min(1.0, (article.importance or 0) / 100.0))


def recency_score(article: Article, now: datetime) -> float:
    """Exponential half-life decay on publish (or fetch) time."""
    age = _age_hours(article.published_at or article.fetched_at, now)
    if age is None:
        return 0.0
    return 0.5 ** (age / settings.rank_recency_halflife_hours)


def cluster_score(size: int | None) -> float:
    """0 for a single source; saturates once ~5 outlets cover the story."""
    if not size or size < 2:
        return 0.0
    return min(1.0, (size - 1) / 4.0)


def source_score(category: str | None) -> float:
    return SOURCE_WEIGHT.get(category or "", 0.6)


def rank_score(article: Article, category: str | None, cluster_size: int | None,
               now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (
        settings.rank_w_importance * importance_score(article)
        + settings.rank_w_recency * recency_score(article, now)
        + settings.rank_w_cluster * cluster_score(cluster_size)
        + settings.rank_w_source * source_score(category)
    )
