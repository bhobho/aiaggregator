"""Per-article enrichment: summary, tags, companies, importance via local LLM."""
from __future__ import annotations

import logging
import re
import sqlite3

from .. import db
from ..models import Article
from . import ollama_client

log = logging.getLogger(__name__)

# YouTube video descriptions in particular are full of emoji, promo links and
# smart quotes ("Join our WhatsApp Community 🔗", curly apostrophes, etc.)
# that a small local model tends to echo back verbatim into its JSON response
# — producing malformed JSON far more often than on plain article text (see
# the enrichment success-rate gap between video and text sources). Stripping
# emoji and normalizing quotes before the text ever reaches the prompt removes
# most of that risk at the source, on top of the parser-level fallback in
# ollama_client._parse_json_response.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flag emoji)
    "\uFE0F"                 # variation selector-16 (emoji presentation)
    "]+",
    flags=re.UNICODE,
)
_SMART_QUOTES = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
})


def _sanitize_for_prompt(text: str) -> str:
    """Strip emoji and normalize smart quotes before building an LLM prompt."""
    if not text:
        return text
    text = _EMOJI_RE.sub("", text)
    text = text.translate(_SMART_QUOTES)
    return " ".join(text.split())

# Controlled tag vocabulary keeps tags consistent and filterable.
TAG_VOCAB = [
    "llms", "agents", "safety", "alignment", "funding", "hardware", "chips",
    "research", "product", "policy", "regulation", "open-source", "robotics",
    "multimodal", "benchmark", "infrastructure", "developer-tools", "rag",
]

KNOWN_COMPANIES = [
    "OpenAI", "Anthropic", "Google DeepMind", "Google", "Meta", "Microsoft",
    "AWS", "Amazon", "Nvidia", "Hugging Face", "Mistral", "Cohere", "xAI",
    "Apple", "IBM", "Stability AI", "DeepSeek", "Perplexity", "Moonshot",
    "Alibaba", "ByteDance",
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
        title=_sanitize_for_prompt(article.title),
        summary=_sanitize_for_prompt(article.raw_summary[:1200]) or "(none)",
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


DETAIL_SYSTEM = (
    "You are a precise tech-news writer. Respond ONLY with a compact JSON object."
)

DETAIL_PROMPT_TMPL = """Write a self-contained summary of this news item in 100-160 words.
The summary MUST be at least 100 words. Cover what happened, who is involved, and why it
matters. Neutral, factual, plain prose — no hype, no markdown. If the source material is
thin, add brief, widely-known background about the companies or topic involved to provide
context — but do not invent specifics about this news item itself.

Title: {title}
Short summary: {summary}
Source text: {raw}

Return JSON with EXACTLY one key: "summary".
"""

DETAIL_MIN_WORDS = 100


async def detail_summary(title: str, summary: str | None, raw: str) -> str | None:
    """Generate a 100-160 word reader summary — enough text to stand on its own
    on the post page before the "Read full story" link — or None if Ollama is
    unavailable. Retries with a stronger nudge if the model comes back short."""
    prompt = DETAIL_PROMPT_TMPL.format(
        title=_sanitize_for_prompt(title),
        summary=_sanitize_for_prompt(summary) if summary else "(none)",
        raw=_sanitize_for_prompt(raw[:1200]) or "(none)",
    )
    text = ""
    for _ in range(3):
        try:
            data = await ollama_client.generate_json(prompt, system=DETAIL_SYSTEM)
        except ollama_client.OllamaError:
            return None
        text = str(data.get("summary", "")).strip()
        if len(text.split()) >= DETAIL_MIN_WORDS:
            return text
        prompt += (
            f"\nYour previous answer was only {len(text.split())} words — too short. "
            f"Write AT LEAST {DETAIL_MIN_WORDS} words, adding relevant background "
            "context about the companies or topic so the piece stands on its own."
        )
    return text or None


async def run_enrichment(conn: sqlite3.Connection, limit: int) -> int:
    """Enrich up to `limit` pending articles. Returns count enriched.

    A small slice of every pass is reserved for retrying previously-failed
    articles (see db.pending_retry_enrichment), separate from the 'new' queue
    — otherwise a low-volume category's failed backlog would never get a turn
    against a continuous stream of fresh high-volume content."""
    if not await ollama_client.is_available():
        log.warning("Ollama not available; skipping enrichment pass")
        return 0
    retry_quota = min(3, limit)
    pending = (db.pending_enrichment(conn, limit - retry_quota)
              + db.pending_retry_enrichment(conn, retry_quota))
    done = 0
    for article in pending:
        if await enrich_article(conn, article):
            done += 1
    if done:
        log.info("enriched %d articles", done)
    return done


async def run_detail_backfill(conn: sqlite3.Connection, limit: int) -> int:
    """Fill in the long reader summary (100+ words) for enriched articles that
    don't have one yet, so the post page always has enough text to show before
    the "Read full story" link. Returns count filled in."""
    if not await ollama_client.is_available():
        return 0
    pending = db.pending_detail_backfill(conn, limit)
    done = 0
    for article in pending:
        text = await detail_summary(article.title, article.summary, article.raw_summary or "")
        if text:
            db.save_detail_summary(conn, article.id, text)
            done += 1
    if done:
        log.info("detail-summary backfill: filled %d/%d", done, len(pending))
    return done
