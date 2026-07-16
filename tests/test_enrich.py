import pytest

from aiaggregator import db, queries
from aiaggregator.enrich import cluster, summarize
from aiaggregator.enrich import ollama_client
from aiaggregator.ingest.normalize import content_hash
from aiaggregator.models import Article, now_iso


def _add(conn, source_id, title, url, summary=""):
    a = Article(source_id=source_id, guid=url, url=url, title=title,
                content_hash=content_hash(title, url), fetched_at=now_iso(),
                raw_summary=summary, published_at=now_iso())
    return db.insert_article(conn, a)


async def test_enrichment_cleans_and_saves(conn, source_id, monkeypatch):
    aid = _add(conn, source_id, "OpenAI launches new model", "https://x/1", "details")

    async def fake_available():
        return True

    async def fake_generate(prompt, **kw):
        return {
            "summary": "OpenAI released a model.",
            "tags": ["llms", "PRODUCT", "not-a-real-tag"],  # mixed case + invalid
            "companies": ["openai", "Acme"],                 # unknown filtered out
            "importance": 150,                               # clamped to 100
        }

    monkeypatch.setattr(ollama_client, "is_available", fake_available)
    monkeypatch.setattr(ollama_client, "generate_json", fake_generate)

    n = await summarize.run_enrichment(conn, limit=10)
    assert n == 1

    art = db.recent_articles(conn, days=2)[0]
    assert art.status == "enriched"
    assert art.summary == "OpenAI released a model."
    assert art.tags == ["llms", "product"]
    assert art.companies == ["OpenAI"]
    assert art.importance == 100


async def test_failed_enrichment_marks_status(conn, source_id, monkeypatch):
    _add(conn, source_id, "Some news", "https://x/2")

    async def fake_available():
        return True

    async def boom(prompt, **kw):
        raise ollama_client.OllamaError("down")

    monkeypatch.setattr(ollama_client, "is_available", fake_available)
    monkeypatch.setattr(ollama_client, "generate_json", boom)

    await summarize.run_enrichment(conn, limit=10)
    art = db.recent_articles(conn, days=2)[0]
    assert art.status == "failed"


async def test_clustering_groups_duplicates(conn, source_id, monkeypatch):
    s2 = db.upsert_source(conn, __import__("aiaggregator.models", fromlist=["Source"]).Source(
        name="News B", url="https://b/feed", category="news"))
    _add(conn, source_id, "GPT-5 released with major reasoning gains today",
         "https://x/gpt5a", "OpenAI announced GPT-5 reasoning improvements")
    _add(conn, s2, "OpenAI announces GPT-5 with major reasoning gains",
         "https://x/gpt5b", "GPT-5 reasoning improvements announced by OpenAI")
    _add(conn, source_id, "Nvidia unveils new datacenter GPU architecture",
         "https://x/nv", "Brand new GPU silicon for datacenters")

    # no embed model in tests -> exercises the TF-IDF fallback path
    async def no_models():
        return []
    monkeypatch.setattr(ollama_client, "list_models", no_models)

    formed = await cluster.recluster(conn)
    assert formed == 1  # the two GPT-5 stories cluster; Nvidia stays a singleton

    arts = db.recent_articles(conn, days=2)
    clustered = [a for a in arts if a.cluster_id is not None]
    assert len(clustered) == 2

    groups = queries.group_clusters(arts)
    multi = [g for g in groups if g["extras"]]
    assert len(multi) == 1
    assert len(multi[0]["extras"]) == 1


def test_fts_search(conn, source_id):
    _add(conn, source_id, "Anthropic ships Claude update", "https://x/c1", "agentic stuff")
    _add(conn, source_id, "Totally unrelated gardening post", "https://x/g1", "plants")
    res = queries.feed(conn, queries.FeedFilters(search="claude"))
    titles = [a.title for a in res]
    assert "Anthropic ships Claude update" in titles
    assert "Totally unrelated gardening post" not in titles
