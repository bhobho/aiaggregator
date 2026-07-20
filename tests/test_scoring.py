from aiaggregator import scoring
from aiaggregator.models import Article, now_iso


def _art(importance=0, tags=None, companies=None):
    return Article(source_id=1, guid="g", url="u", title="t", content_hash="h",
                   importance=importance, tags=tags or [], companies=companies or [],
                   published_at=now_iso(), fetched_at=now_iso())


def test_scores_are_clamped_0_100():
    a = _art(importance=100, tags=list(scoring.TECH_TAGS), companies=["OpenAI"])
    assert 0 <= scoring.technical_impact(a, "lab") <= 100
    assert 0 <= scoring.business_impact(a, "market", cluster_size=5) <= 100


def test_technical_tags_raise_technical_score():
    plain = _art(importance=40)
    techy = _art(importance=40, tags=["llms", "agents", "infrastructure"])
    assert scoring.technical_impact(techy, "research") > scoring.technical_impact(plain, "research")


def test_business_signals_raise_business_score():
    plain = _art(importance=40)
    bizzy = _art(importance=40, tags=["funding", "regulation"], companies=["Anthropic"])
    assert (scoring.business_impact(bizzy, "market", cluster_size=4)
            > scoring.business_impact(plain, "news", cluster_size=1))


def test_cluster_size_boosts_business_impact():
    a = _art(importance=50, tags=["product"])
    assert scoring.business_impact(a, "market", cluster_size=6) >= scoring.business_impact(a, "market", 1)


def test_tier_thresholds():
    assert scoring.tier(90)[0] == "Critical"
    assert scoring.tier(65)[0] == "High"
    assert scoring.tier(40)[0] == "Moderate"
    assert scoring.tier(10)[0] == "Low"


def test_score_article_bundle_has_all_keys():
    a = _art(importance=75, tags=["agents"], companies=["OpenAI"])
    s = scoring.score_article(a, "lab", cluster_size=3)
    for k in ("technical", "business", "technical_label", "business_token", "why", "action"):
        assert k in s
    assert isinstance(s["why"], str) and s["why"]
    assert isinstance(s["action"], str) and s["action"]


def test_recommended_action_reflects_dominant_tag():
    a = _art(importance=85, tags=["agents"])
    action = scoring.recommended_action(a, technical=85, business=30)
    assert "agent" in action.lower()
