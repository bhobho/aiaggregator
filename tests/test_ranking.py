from datetime import datetime, timezone

from aiaggregator import ranking
from aiaggregator.models import Article, now_iso


def _art(importance, published=None):
    return Article(source_id=1, guid="g", url="u", title="t", content_hash="h",
                   importance=importance, published_at=published, fetched_at=now_iso())


def test_cluster_size_boosts_rank():
    now = datetime.now(timezone.utc)
    a = _art(60, now.isoformat())
    solo = ranking.rank_score(a, "news", cluster_size=1, now=now)
    multi = ranking.rank_score(a, "news", cluster_size=5, now=now)
    assert multi > solo  # covered by many outlets ranks higher


def test_recency_decay():
    now = datetime.now(timezone.utc)
    fresh = _art(60, now.isoformat())
    old = _art(60, "2020-01-01T00:00:00+00:00")
    assert ranking.rank_score(fresh, "news", 1, now) > ranking.rank_score(old, "news", 1, now)


def test_source_trust_orders_labs_above_community():
    now = datetime.now(timezone.utc)
    a = _art(60, now.isoformat())
    assert ranking.rank_score(a, "lab", 1, now) > ranking.rank_score(a, "community", 1, now)


def test_high_importance_recent_clustered_beats_stale_solo():
    now = datetime.now(timezone.utc)
    big = _art(80, now.isoformat())
    stale = _art(85, "2020-01-01T00:00:00+00:00")
    # Despite slightly lower LLM importance, the fresh, multi-source story wins.
    assert ranking.rank_score(big, "lab", 4, now) > ranking.rank_score(stale, "lab", 1, now)
