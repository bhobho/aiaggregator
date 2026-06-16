import pytest

from aiaggregator import db
from aiaggregator.models import Source


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def source_id(conn):
    return db.upsert_source(
        conn, Source(name="Test Lab", url="https://example.com/feed", category="lab",
                     company="OpenAI")
    )


SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test</title>
  <item>
    <title>OpenAI ships a new agent framework</title>
    <link>https://example.com/a1</link>
    <guid>https://example.com/a1</guid>
    <description>A &lt;b&gt;big&lt;/b&gt; release for agents.</description>
    <pubDate>Mon, 02 Jun 2025 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Weather is nice today</title>
    <link>https://example.com/a2</link>
    <guid>https://example.com/a2</guid>
    <description>Not about AI at all.</description>
    <pubDate>Mon, 02 Jun 2025 11:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""
