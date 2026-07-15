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


def test_source_hint_forces_bucket():
    a = _art("Weekly roundup")  # no keyword hit
    assert _slugs(a) == set()
    assert "finance" in _slugs(a, source_name="AI Funding & Investment")


def test_body_fallback_matches():
    a = _art("Quiet news day", summary="The company closed a Series B funding round.")
    assert "finance" in _slugs(a)


def test_source_hints_reference_real_slugs():
    assert set(market.SOURCE_HINTS.values()) == set(market.BY_SLUG)


def test_dedupe_stories_drops_same_url_and_title():
    a1 = _art("Acme raises $10M", url="https://a/1")
    a2 = _art("Acme raises $10M", url="https://b/1")      # same title, other outlet feed
    a3 = _art("Different headline", url="https://a/1")    # same url
    a4 = _art("Fresh story", url="https://c/1")
    out = queries.dedupe_stories([a1, a2, a3, a4])
    assert [a.title for a in out] == ["Acme raises $10M", "Fresh story"]
