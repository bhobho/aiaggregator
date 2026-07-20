"""Deterministic technical / business impact scoring for the intelligence UI.

The enrichment pipeline gives each article a single 0-100 ``importance``. The
platform surfaces TWO complementary lenses instead — a *technical* impact score
(does this matter to the people building AI systems?) and a *business* impact
score (does this matter to the people funding and deciding on AI?) — plus a
templated "why it matters" line and a recommended next action.

Everything here is a pure function of already-stored signals (importance, tags,
source category, cluster size). No LLM calls, so it is instant, offline, and
testable. Richer per-article reasoning is layered on top via the on-demand LLM
perspectives (see ``enrich/perspectives.py``).
"""
from __future__ import annotations

from .models import Article

# Tags that signal engineering / research significance.
TECH_TAGS = {
    "llms", "agents", "infrastructure", "developer-tools", "rag", "multimodal",
    "research", "hardware", "chips", "open-source", "benchmark", "robotics",
}
# Tags that signal commercial / strategic significance.
BIZ_TAGS = {
    "funding", "policy", "regulation", "product", "safety", "alignment",
}

# Source categories weighted toward each lens.
TECH_CATEGORIES = {"lab": 12, "research": 14, "architecture": 12, "community": 6,
                   "news": 4}
BIZ_CATEGORIES = {"market": 14, "industry": 14, "lab": 6, "news": 4}

_TIERS = (
    (80, "Critical", "critical"),
    (60, "High", "high"),
    (35, "Moderate", "moderate"),
    (0, "Low", "low"),
)


def _clamp(v: float) -> int:
    return int(max(0, min(100, round(v))))


def technical_impact(a: Article, category: str | None = None) -> int:
    """0-100: significance to engineers / architects / researchers."""
    score = 0.55 * (a.importance or 0)
    tags = set(a.tags)
    score += 9 * len(tags & TECH_TAGS)
    score += TECH_CATEGORIES.get(category or "", 0)
    return _clamp(score)


def business_impact(a: Article, category: str | None = None,
                    cluster_size: int | None = None) -> int:
    """0-100: significance to product leaders / executives / investors."""
    score = 0.55 * (a.importance or 0)
    tags = set(a.tags)
    score += 11 * len(tags & BIZ_TAGS)
    score += BIZ_CATEGORIES.get(category or "", 0)
    if a.companies:
        score += 6  # names a known company → more market-relevant
    if cluster_size and cluster_size >= 2:
        score += min(12, 4 * (cluster_size - 1))  # broad coverage = broad relevance
    return _clamp(score)


def tier(score: int) -> tuple[str, str]:
    """(label, css-token) for a 0-100 score."""
    for threshold, label, token in _TIERS:
        if score >= threshold:
            return label, token
    return "Low", "low"


# ---- narrative helpers (templated, deterministic) --------------------------

# Dominant-signal → (why-it-matters phrase, recommended action) keyed on tag.
_TAG_NARRATIVE = {
    "agents": ("shifts what autonomous agent systems can do",
               "Evaluate against your agent / orchestration stack."),
    "llms": ("moves the frontier of model capability and cost",
             "Re-benchmark your model choices and pricing assumptions."),
    "rag": ("changes retrieval-augmented patterns teams rely on",
            "Review your RAG pipeline and eval harness."),
    "developer-tools": ("affects day-to-day developer workflows",
                        "Trial with a small engineering group before rollout."),
    "infrastructure": ("reshapes the cost and scale of running AI",
                       "Model the impact on your inference / infra budget."),
    "open-source": ("expands what teams can self-host and control",
                    "Assess build-vs-buy against this open option."),
    "funding": ("signals where capital and competition are heading",
                "Factor into competitive and partnership strategy."),
    "policy": ("changes the regulatory ground rules",
               "Brief legal / governance on compliance exposure."),
    "regulation": ("changes the regulatory ground rules",
                   "Brief legal / governance on compliance exposure."),
    "product": ("puts a new capability in customers' hands",
                "Scan for product and go-to-market implications."),
    "safety": ("affects trust, risk, and responsible-AI posture",
               "Review against your AI governance and risk controls."),
    "robotics": ("advances embodied / physical AI",
                 "Watch for adjacency to your industry's automation."),
    "multimodal": ("extends AI across text, image, audio, and video",
                   "Explore multimodal use cases for your product."),
    "hardware": ("shifts the compute supply and economics",
                 "Revisit capacity planning and vendor commitments."),
    "chips": ("shifts the compute supply and economics",
              "Revisit capacity planning and vendor commitments."),
    "research": ("previews capabilities heading toward production",
                 "Track for a 6–12 month roadmap horizon."),
    "benchmark": ("recalibrates how models are compared",
                  "Update your evaluation criteria accordingly."),
}
_DEFAULT_NARRATIVE = ("is a notable development in the AI landscape",
                      "Monitor for follow-on developments.")


def _dominant_tag(a: Article) -> str | None:
    for t in a.tags:  # tags are ordered most-relevant-first from enrichment
        if t in _TAG_NARRATIVE:
            return t
    return None


def why_it_matters(a: Article) -> str:
    """One-line 'why it matters', derived from the dominant signal."""
    company = a.companies[0] if a.companies else "This"
    phrase = _TAG_NARRATIVE.get(_dominant_tag(a) or "", _DEFAULT_NARRATIVE)[0]
    subject = f"{company}'s move" if a.companies else "This"
    return f"{subject} {phrase}."


def recommended_action(a: Article, technical: int, business: int) -> str:
    """A concrete next step, keyed on the dominant tag and score tier."""
    action = _TAG_NARRATIVE.get(_dominant_tag(a) or "", _DEFAULT_NARRATIVE)[1]
    if max(technical, business) >= 80:
        return f"Act now: {action}"
    if max(technical, business) < 35:
        return f"Low priority — {action.lower()}"
    return action


def score_article(a: Article, category: str | None = None,
                  cluster_size: int | None = None) -> dict:
    """Bundle every derived signal for template use."""
    tech = technical_impact(a, category)
    biz = business_impact(a, category, cluster_size)
    tech_label, tech_token = tier(tech)
    biz_label, biz_token = tier(biz)
    return {
        "technical": tech,
        "business": biz,
        "technical_label": tech_label,
        "technical_token": tech_token,
        "business_label": biz_label,
        "business_token": biz_token,
        "why": why_it_matters(a),
        "action": recommended_action(a, tech, biz),
    }
