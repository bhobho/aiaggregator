from aiaggregator import db, trends
from aiaggregator.ingest.normalize import content_hash
from aiaggregator.models import Article, now_iso


def _insert(conn, source_id, title, summary="", tags=None):
    a = Article(source_id=source_id, guid=title, url="https://x/" + title.replace(" ", "-"),
                title=title, raw_summary=summary, content_hash=content_hash(title, summary),
                published_at=now_iso(), fetched_at=now_iso())
    aid = db.insert_article(conn, a)
    if tags:
        db.save_enrichment(conn, aid, summary=summary, tags=tags, companies=[], importance=50)
    return aid


def test_trend_matches_by_keyword():
    t = trends.BY_SLUG["ai-agents"]
    hit = Article(source_id=1, guid="g", url="u", content_hash="h",
                  title="A new autonomous agent framework", raw_summary="")
    miss = Article(source_id=1, guid="g2", url="u2", content_hash="h2",
                   title="Quarterly earnings roundup", raw_summary="")
    assert t.matches(hit)
    assert not t.matches(miss)


def test_trend_matches_by_tag():
    t = trends.BY_SLUG["ai-infrastructure"]
    a = Article(source_id=1, guid="g", url="u", content_hash="h",
                title="Unrelated headline", raw_summary="", tags=["infrastructure"])
    assert t.matches(a)


def test_compute_trends_shape_and_counts(conn, source_id):
    _insert(conn, source_id, "OpenAI launches an agentic tool-use platform")
    _insert(conn, source_id, "New GPU cluster boosts inference throughput")
    data = trends.compute_trends(conn)
    assert len(data) == len(trends.TRENDS)
    for d in data:
        assert {"trend", "count", "momentum", "maturity", "impact", "outlook", "top"} <= set(d)
    by_name = {d["trend"].slug: d for d in data}
    assert by_name["ai-agents"]["count"] >= 1
    assert by_name["ai-infrastructure"]["count"] >= 1


def test_radar_bars_normalized(conn, source_id):
    _insert(conn, source_id, "Agentic AI systems take on multi-step workflows")
    rows = trends.radar(conn)
    assert len(rows) == len(trends.TRENDS)
    assert all(0 <= r["bar"] <= 100 for r in rows)
