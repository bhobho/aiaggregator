"""Per-article enrichment: summary, tags, companies, importance via local LLM."""
from __future__ import annotations

import logging
import sqlite3

from .. import db
from ..models import Article
from . import ollama_client

log = logging.getLogger(__name__)

# Controlled tag vocabulary keeps tags consistent and filterable.
TAG_VOCAB = [
    "llms", "agents", "safety", "alignment", "funding", "hardware", "chips",
    "research", "product", "policy", "regulation", "open-source", "robotics",
    "multimodal", "benchmark", "infrastructure", "developer-tools", "rag",
]

KNOWN_COMPANIES = [
    "OpenAI", "Anthropic", "Google DeepMind", "Google", "Meta", "Microsoft",
    "AWS", "Amazon", "Nvidia", "Hugging Face", "Mistral", "Cohere", "xAI",
    "Apple", "IBM", "Stability AI",
]

SYSTEM = (
    "You are a precise tech-news analyst. Given an AI/tech news item, respond ONLY "
    "with a compact JSON object. Be factual and terse."
)

PROMPT_TMPL = """Analyze this AI/tech news item.

Title: {title}
Source summary: {summary}

Return JSON with EXACTLY these keys:
- "summary": one or two sentences, <= 40 words, neutral and factual.
- "tags": array of 1-4 strings chosen ONLY from this list: {vocab}.
- "companies": array of organizations mentioned, chosen ONLY from this list when applicable: {companies}. Use [] if none apply.
- "importance": integer 0-100 for how significant this is to the AI field (major model/funding/policy news high; routine items low).
"""


def _clean_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        return []
    out = []
    for t in tags:
        t = str(t).strip().lower()
        if t in TAG_VOCAB and t not in out:
            out.append(t)
    return out[:4]


def _clean_companies(companies) -> list[str]:
    if not isinstance(companies, list):
        return []
    lookup = {c.lower(): c for c in KNOWN_COMPANIES}
    out = []
    for c in companies:
        key = str(c).strip().lower()
        if key in lookup and lookup[key] not in out:
            out.append(lookup[key])
    return out


def _clean_importance(val) -> int:
    try:
        return max(0, min(100, int(val)))
    except (TypeError, ValueError):
        return 0


async def enrich_article(conn: sqlite3.Connection, article: Article) -> bool:
    prompt = PROMPT_TMPL.format(
        title=article.title,
        summary=article.raw_summary[:1200] or "(none)",
        vocab=", ".join(TAG_VOCAB),
        companies=", ".join(KNOWN_COMPANIES),
    )
    try:
        data = await ollama_client.generate_json(prompt, system=SYSTEM)
    except ollama_client.OllamaError:
        db.mark_failed(conn, article.id)
        return False

    summary = str(data.get("summary", "")).strip() or article.raw_summary[:200]
    db.save_enrichment(
        conn, article.id,
        summary=summary,
        tags=_clean_tags(data.get("tags")),
        companies=_clean_companies(data.get("companies")),
        importance=_clean_importance(data.get("importance")),
    )
    return True


async def run_enrichment(conn: sqlite3.Connection, limit: int) -> int:
    """Enrich up to `limit` pending articles. Returns count enriched."""
    if not await ollama_client.is_available():
        log.warning("Ollama not available; skipping enrichment pass")
        return 0
    pending = db.pending_enrichment(conn, limit)
    done = 0
    for article in pending:
        if await enrich_article(conn, article):
            done += 1
    if done:
        log.info("enriched %d articles", done)
    return done
