from aiaggregator import market, queries
from aiaggregator.models import Article


def _art(title, url="https://x/1", summary="", tags=None):
    return Article(source_id=1, guid=url, url=url, title=title,
                   content_hash="h", raw_summary=summary, tags=tags or [])


def _slugs(a, source_name=None):
    return {c.slug for c in market.categories_for(a, source_name)}


def test_title_matching_assigns_categories():
    assert "finance" in _slugs(_art("Startup raises $200 million at $2 billion valuation"))
    assert "regulation" in _slugs(_art("EU AI Act compliance deadline looms for chatbot makers"))
    assert "ma" in _slugs(_art("Nvidia acquires AI networking company"))
    assert "partnerships" in _slugs(_art("OpenAI partners with telecom giant"))
    assert "infrastructure" in _slugs(_art("Microsoft to build gigawatt data center for AI compute"))
    assert "security" in _slugs(_art("Deepfake scams surge as AI tools spread"))
    assert "startups" in _slugs(_art("Stealth AI startup emerges with star founders"))


def test_article_can_belong_to_multiple_categories():
    slugs = _slugs(_art("AI startup raises $50 million to build data centers"))
    assert {"finance", "startups", "infrastructure"} <= slugs


def test_source_hint_forces_bucket(monkeypatch):
    a = _art("Weekly roundup")  # no keyword hit
    assert _slugs(a) == set()
    monkeypatch.setitem(market.SOURCE_HINTS, "Funding Newsletter", "finance")
    assert "finance" in _slugs(a, source_name="Funding Newsletter")


def test_body_fallback_matches():
    a = _art("Quiet news day", summary="The company closed a Series B funding round.")
    assert "finance" in _slugs(a)


def test_source_hints_reference_real_slugs():
    assert set(market.SOURCE_HINTS.values()) <= set(market.BY_SLUG)


def test_dedupe_stories_drops_same_url_and_title():
    a1 = _art("Acme raises $10M", url="https://a/1")
    a2 = _art("Acme raises $10M", url="https://b/1")      # same title, other outlet feed
    a3 = _art("Different headline", url="https://a/1")    # same url
    a4 = _art("Fresh story", url="https://c/1")
    out = queries.dedupe_stories([a1, a2, a3, a4])
    assert [a.title for a in out] == ["Acme raises $10M", "Fresh story"]


def test_dedupe_stories_collapses_outlet_suffix_variants():
    a1 = _art("Anthropic launches free Claude for Teachers - The Hill", url="https://a/1")
    a2 = _art("Anthropic launches free Claude for Teachers - AOL.com", url="https://b/1")
    a3 = _art("Anthropic Launches Free Claude for Teachers", url="https://c/1")
    out = queries.dedupe_stories([a1, a2, a3])
    assert len(out) == 1
    assert out[0].url == "https://a/1"  # best-ranked (first) copy kept


def test_strip_outlet_suffix_keeps_content_suffixes():
    from aiaggregator import textnorm
    assert textnorm.strip_outlet_suffix("Big story - TechCrunch") == "Big story"
    assert textnorm.strip_outlet_suffix("Nova Act guide – Part 2") == "Nova Act guide – Part 2"
    assert textnorm.strip_outlet_suffix("No suffix here") == "No suffix here"


def test_industry_feed_drops_paywalled_outlets(conn):
    from aiaggregator import db
    from aiaggregator.ingest.normalize import content_hash
    from aiaggregator.models import Source, now_iso
    sid = db.upsert_source(conn, Source(
        name="Gartner Artificial Intelligence",
        url="https://news.google.com/rss/search?q=gartner", category="industry"))

    def add(title, url):
        db.insert_article(conn, Article(
            source_id=sid, guid=url, url=url, title=title,
            content_hash=content_hash(title, url), fetched_at=now_iso(),
            published_at=now_iso()))

    add("AI spend to double, Gartner says - Reuters", "https://news.google.com/x1")
    add("Enterprises rethink AI budgets - Bloomberg", "https://news.google.com/x2")
    add("The state of enterprise AI - Business Insider", "https://news.google.com/x3")

    titles = [a.title for a in queries.industry_feed(conn)]
    assert any("Reuters" in t for t in titles)          # free outlet kept
    assert not any("Bloomberg" in t for t in titles)    # paywalled dropped
    assert not any("Business Insider" in t for t in titles)


def test_architecture_feed_excludes_github_links(conn):
    from aiaggregator import db
    from aiaggregator.ingest.normalize import content_hash
    from aiaggregator.models import Source, now_iso
    sid = db.upsert_source(conn, Source(
        name="LangChain Blog", url="https://blog.langchain.com/rss.xml",
        category="architecture"))

    def add(title, url):
        db.insert_article(conn, Article(
            source_id=sid, guid=url, url=url, title=title,
            content_hash=content_hash(title, url), fetched_at=now_iso(),
            published_at=now_iso()))

    add("Real architecture write-up", "https://blog.langchain.com/rag-patterns")
    add("New release v1.2.0", "https://github.com/langchain-ai/langchain/releases/tag/v1.2.0")

    urls = [a.url for a in queries.architecture_feed(conn)]
    assert "https://blog.langchain.com/rag-patterns" in urls
    assert all("github.com" not in u for u in urls)


def test_unique_stories_collapses_same_cluster():
    a1 = _art("Story headline one", url="https://a/1")
    a2 = _art("Totally different words", url="https://b/1")
    a1.cluster_id = a2.cluster_id = 7
    a3 = _art("Unrelated story", url="https://c/1")
    out = queries.unique_stories([a1, a2, a3], limit=8)
    assert [a.url for a in out] == ["https://a/1", "https://c/1"]
