from aiaggregator.ingest.normalize import content_hash, parse_feed, strip_html
from tests.conftest import SAMPLE_RSS


def test_strip_html():
    assert strip_html("<b>hello</b>  world") == "hello world"
    assert strip_html(None) == ""


def test_parse_feed_basic():
    arts = parse_feed(SAMPLE_RSS, source_id=1)
    assert len(arts) == 2
    a = arts[0]
    assert a.title == "OpenAI ships a new agent framework"
    assert a.url == "https://example.com/a1"
    assert "big release" in a.raw_summary
    assert a.published_at is not None and a.published_at.startswith("2025-06-02")
    assert a.content_hash == content_hash(a.title, a.url)


def test_community_keyword_filter():
    # Only the AI item survives the keyword filter for community feeds.
    arts = parse_feed(SAMPLE_RSS, source_id=1, is_community=True,
                      keywords=["agent", "openai"])
    titles = [a.title for a in arts]
    assert "OpenAI ships a new agent framework" in titles
    assert "Weather is nice today" not in titles


def test_content_hash_stable():
    h1 = content_hash("Same Title", "https://x/1")
    h2 = content_hash("same   title", "https://x/1")
    assert h1 == h2  # case + whitespace normalized
