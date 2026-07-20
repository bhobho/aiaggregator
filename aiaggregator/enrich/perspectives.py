"""On-demand multi-perspective reader content via the local LLM.

Mirrors ``summarize.detail_summary``: generate once, on first open, and cache the
JSON on the article row (``perspectives`` column). Every story is explained from
five angles so one reader serves engineers through executives:

    quick        — 30-second plain-language understanding
    technical    — how it works
    architecture — how it can be implemented / integrated
    business     — why it matters commercially
    leadership   — what decision to make

Returns ``None`` when Ollama is unavailable so the caller can fall back to a
templated view built from the stored summary.
"""
from __future__ import annotations

from . import ollama_client

KEYS = ("quick", "technical", "architecture", "business", "leadership")

SYSTEM = (
    "You are a senior AI analyst writing for a briefing read by engineers, "
    "architects, product leaders, and executives. Respond ONLY with a compact "
    "JSON object. Be factual, specific, and concise — no hype, no markdown."
)

PROMPT_TMPL = """Explain this AI/tech news item from five perspectives.

Title: {title}
Summary: {summary}
Source text: {raw}

Return JSON with EXACTLY these five keys, each a 1-2 sentence string (<= 45 words):
- "quick": a 30-second plain-language explanation anyone can follow.
- "technical": how the technology actually works or what changed technically.
- "architecture": how a team could implement or integrate this (patterns, tools).
- "business": why it matters commercially — market, customers, competition.
- "leadership": the strategic decision or action a leader should consider.
If the source is thin, add brief widely-known context, but do not invent specifics.
"""


def _fallback(summary: str, raw: str) -> dict[str, str]:
    """Templated views when the LLM is unavailable — honest framing over the
    stored summary rather than fabricated analysis."""
    base = (summary or raw or "").strip() or "No summary is available for this item yet."
    return {
        "quick": base,
        "technical": f"Technical detail isn't generated yet. In brief: {base}",
        "architecture": ("Open the source for implementation specifics. "
                         f"Context: {base}"),
        "business": f"Business context (auto-summary): {base}",
        "leadership": ("Review the source and weigh relevance to your roadmap. "
                       f"Context: {base}"),
    }


def _clean(data: dict, summary: str, raw: str) -> dict[str, str]:
    fb = _fallback(summary, raw)
    out = {}
    for k in KEYS:
        val = str(data.get(k, "")).strip()
        out[k] = val or fb[k]
    return out


async def perspectives(title: str, summary: str | None, raw: str) -> dict[str, str]:
    """Generate the five perspectives, or a templated fallback if Ollama is down."""
    prompt = PROMPT_TMPL.format(
        title=title,
        summary=summary or "(none)",
        raw=(raw or "")[:1200] or "(none)",
    )
    try:
        data = await ollama_client.generate_json(prompt, system=SYSTEM)
    except ollama_client.OllamaError:
        return _fallback(summary or "", raw or "")
    if not isinstance(data, dict):
        return _fallback(summary or "", raw or "")
    return _clean(data, summary or "", raw or "")
