from aiaggregator import db, queries
from aiaggregator.ingest.normalize import content_hash
from aiaggregator.models import Article, now_iso


def _add(conn, source_id, title, url, tags):
    aid = db.insert_article(conn, Article(
        source_id=source_id, guid=url, url=url, title=title,
        content_hash=content_hash(title, url), fetched_at=now_iso(),
        published_at=now_iso()))
    db.save_enrichment(conn, aid, summary="", tags=tags, companies=[], importance=50)
    return aid


def test_topic_feed_filters_by_tag(conn, source_id):
    _add(conn, source_id, "New agent framework ships", "https://x/1", ["agents"])
    _add(conn, source_id, "Model card released", "https://x/2", ["llms"])
    _add(conn, source_id, "Agentic and multimodal update", "https://x/3", ["agents", "multimodal"])

    titles = {a.title for a in queries.topic_feed(conn, "agents")}
    assert titles == {"New agent framework ships", "Agentic and multimodal update"}
    assert queries.topic_feed(conn, "robotics") == []


def test_topic_feed_does_not_match_substring_tags(conn, source_id):
    # "rag" must not incidentally match an article only tagged "storage" or similar —
    # the JSON-array LIKE pattern requires the exact quoted tag.
    _add(conn, source_id, "Retrieval pipeline overhaul", "https://x/1", ["rag"])
    _add(conn, source_id, "Storage layer redesign", "https://x/2", ["infrastructure"])
    titles = {a.title for a in queries.topic_feed(conn, "rag")}
    assert titles == {"Retrieval pipeline overhaul"}


def test_top_topic_respects_limit(conn, source_id):
    for i in range(5):
        _add(conn, source_id, f"Story {i}", f"https://x/{i}", ["safety"])
    assert len(queries.top_topic(conn, "safety", limit=3)) == 3
