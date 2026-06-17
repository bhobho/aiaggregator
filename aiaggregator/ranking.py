"""Composite story ranking.

Blends four signals into a single 0..~1 score so "Top stories" reflects more than
the LLM's raw opinion:

    rank = w1*importance + w2*recency + w3*cluster_size + w4*source_trust

Each component is normalized to 0..1; weights come from settings and need not sum to 1.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import settings
from .models import Article

# Source-type trust: first-party labs > major outlets > community aggregators.
SOURCE_WEIGHT = {"lab": 1.0, "research": 0.9, "news": 0.75, "community": 0.5}

# Signals that an item is a product / framework / agentic-architecture announcement.
ANNOUNCE_TAGS = {
    "product", "agents", "developer-tools", "open-source", "infrastructure",
    "rag", "multimodal", "robotics",
}
ANNOUNCE_KEYWORDS = (
    "launch", "introduc", "releas", "announc", "unveil", "now available",
    "available now", "framework", "sdk", "toolkit", "platform", "agent",
    "agentic", "open-source", "open source", "open-weight", "open weights",
    "rolls out", "debut", "ships",
)

# Priority themes the user wants surfaced. Each theme is a regex (word-boundary aware)
# matched against the title (strong) and summary/tags (weaker).
_THEME_PATTERNS = {
    "models": r"foundation model|frontier model|language model|\bllms?\b|multimodal|"
              r"reasoning model|small language model|\bslms?\b|open[- ]weight|"
              r"mixture[- ]of[- ]experts|\bmoe\b|"
              r"\bgpt-?\d|\bclaude\b|\bgemini\b|\bllama\b|\bqwen\b|\bdeepseek\b|\bmistral\b|"
              r"\bkimi\b|moonshot|perplexity|\bgrok\b|\bphi-?\d|\bnemotron\b|"
              r"new .{0,14}model|model (family|release|launch)",
    "agents": r"\bagent(s|ic)?\b|autonomous|multi[- ]agent|tool[- ]use|tool[- ]calling|computer use",
    "inference": r"inference|\bserving\b|\bvllm\b|tensorrt|\bsglang\b|throughput|latency|"
                 r"quantiz|kv[- ]cache|speculative decoding|tokens? per second",
    "mcp": r"model context protocol|\bmcp\b",
    "finetune_safety": r"fine[- ]?tun|\blora\b|\brlhf\b|\bdpo\b|alignment|evaluation|\beval(s|uations?)?\b|"
                       r"benchmark|red[- ]team|guardrail|\bsafety\b|jailbreak|hallucinat",
    "infra": r"infrastructure|deployment|\bdeploy\b|\bgpu(s)?\b|data ?center|\bcluster\b|"
             r"kubernetes|serverless",
    "frameworks": r"framework|\bsdk\b|\bapis?\b|\blibrary\b|toolkit|open[- ]source|"
                  r"\bv\d+(\.\d+)?\b|major (release|version|upgrade)|\bupgrade\b",
    "devtools": r"coding assistant|code assistant|copilot|\bide\b|ci/cd|\bdevops\b|"
                r"platform engineering|automation|\bpipeline\b|orchestrat|developer (tool|platform)",
}
_THEME_RES = {name: re.compile(pat, re.I) for name, pat in _THEME_PATTERNS.items()}


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def importance_score(article: Article) -> float:
    return max(0.0, min(1.0, (article.importance or 0) / 100.0))


def recency_score(article: Article, now: datetime) -> float:
    """Exponential half-life decay on publish (or fetch) time."""
    age = _age_hours(article.published_at or article.fetched_at, now)
    if age is None:
        return 0.0
    return 0.5 ** (age / settings.rank_recency_halflife_hours)


def cluster_score(size: int | None) -> float:
    """0 for a single source; saturates once ~5 outlets cover the story."""
    if not size or size < 2:
        return 0.0
    return min(1.0, (size - 1) / 4.0)


def source_score(category: str | None) -> float:
    return SOURCE_WEIGHT.get(category or "", 0.6)


def announcement_score(article: Article) -> float:
    """1.0 for clear product/framework/agentic announcements, else lower."""
    score = 0.0
    if set(article.tags) & ANNOUNCE_TAGS:
        score += 0.6
    title = (article.title or "").lower()
    if any(k in title for k in ANNOUNCE_KEYWORDS):
        score += 0.6
    return min(1.0, score)


def priority_score(article: Article) -> float:
    """How well the item matches the prioritized themes (new models, agents,
    inference/serving, MCP, fine-tuning/alignment/eval/safety, AI infra, and
    dev frameworks/SDKs/open-source/coding/DevOps). Title hits count most."""
    title = (article.title or "")
    body = (article.summary or article.raw_summary or "") + " " + " ".join(article.tags)
    title_hits = sum(1 for rx in _THEME_RES.values() if rx.search(title))
    body_hits = sum(1 for rx in _THEME_RES.values() if rx.search(body))
    return min(1.0, 0.5 * title_hits + 0.2 * body_hits)


def rank_score(article: Article, category: str | None, cluster_size: int | None,
               now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    rec = recency_score(article, now)
    ann = announcement_score(article)
    prio = priority_score(article)
    return (
        settings.rank_w_importance * importance_score(article)
        + settings.rank_w_recency * rec
        + settings.rank_w_priority * prio
        + settings.rank_w_cluster * cluster_score(cluster_size)
        + settings.rank_w_source * source_score(category)
        + settings.rank_w_announcement * ann
        # extra kick for on-theme / announcement items that are ALSO fresh ("latest")
        + settings.rank_w_priority * 0.5 * prio * rec
        + settings.rank_w_announcement * 0.5 * ann * rec
    )
