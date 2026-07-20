"""AI trend aggregation — powers the Trends tab and the Briefing 'AI Radar'.

Seven durable trend categories are matched against recent articles by tag and
keyword. For each we compute live signals from the corpus (volume, momentum,
top stories) and pair them with a curated baseline (maturity, outlook) that
reflects where the technology sits in its lifecycle — the lifecycle stage of a
whole field doesn't swing hour to hour, so it is authored, not inferred.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import queries
from .models import Article


class Trend:
    __slots__ = ("slug", "name", "emoji", "blurb", "tags", "pattern",
                 "maturity", "outlook")

    def __init__(self, slug, name, emoji, blurb, tags, keywords, maturity, outlook):
        self.slug = slug
        self.name = name
        self.emoji = emoji
        self.blurb = blurb
        self.tags = set(tags)
        self.pattern = re.compile("|".join(keywords), re.I) if keywords else None
        self.maturity = maturity      # Emerging | Growing | Mainstream | Maturing
        self.outlook = outlook

    def matches(self, a: Article) -> bool:
        if self.tags & set(a.tags):
            return True
        if self.pattern is not None:
            hay = f"{a.title} {a.summary or a.raw_summary or ''}"
            return bool(self.pattern.search(hay))
        return False


TRENDS: list[Trend] = [
    Trend("ai-agents", "AI Agents", "🤖",
          "Autonomous, tool-using systems that plan and act.",
          {"agents"},
          [r"\bagent(s|ic)?\b", "autonomous", "multi[- ]agent", "tool[- ]use",
           "tool[- ]calling", "computer use", "orchestrat"],
          "Growing",
          "Agentic frameworks are consolidating; expect production-grade "
          "orchestration and evaluation tooling to mature over the next year."),
    Trend("generative-ai", "Generative AI", "✨",
          "Text, image, audio, and video generation.",
          {"multimodal", "product"},
          ["generat", "diffusion", "image model", "video model", "text[- ]to[- ]",
           "synthes"],
          "Mainstream",
          "Now embedded across consumer and enterprise products; differentiation "
          "shifts from raw capability to workflow integration and cost."),
    Trend("llms", "LLMs", "🧠",
          "Foundation and frontier language models.",
          {"llms", "benchmark", "open-source"},
          [r"\bllms?\b", "language model", "foundation model", "frontier model",
           "reasoning model", r"\bgpt-?\d", r"\bclaude\b", r"\bgemini\b",
           r"\bllama\b", r"\bqwen\b", "deepseek", "mistral"],
          "Mainstream",
          "Frontier gains are steady but incremental; open-weight models keep "
          "closing the gap, pushing value toward context, tools, and price."),
    Trend("robotics", "Robotics", "🦾",
          "Embodied and physical AI.",
          {"robotics", "hardware"},
          ["robot", "embodied", "humanoid", "manipulation", "autonomous vehicle",
           "self[- ]driving"],
          "Emerging",
          "Foundation models for robotics are early but accelerating; watch for "
          "breakthroughs in generalist manipulation and sim-to-real transfer."),
    Trend("multimodal", "Multimodal AI", "🎛️",
          "Models that span text, vision, audio, and video.",
          {"multimodal"},
          ["multimodal", "vision[- ]language", r"\bvlm\b", "image and text",
           "audio model", "speech model", "text[- ]to[- ]speech"],
          "Growing",
          "Multimodal is becoming the default interface; real-time voice and "
          "video understanding are the next competitive frontier."),
    Trend("enterprise-ai", "Enterprise AI", "🏢",
          "AI adoption, governance, and ROI in organizations.",
          {"product", "policy", "regulation", "safety", "alignment"},
          ["enterprise", "adoption", "\\bROI\\b", "governance", "compliance",
           "deployment", "productivity", "copilot"],
          "Growing",
          "Enterprises move from pilots to production; governance, ROI proof, and "
          "change management become the gating factors, not the technology."),
    Trend("ai-infrastructure", "AI Infrastructure", "⚙️",
          "Compute, chips, serving, and the AI stack.",
          {"infrastructure", "chips", "hardware", "developer-tools", "rag"},
          ["infrastructure", r"\bgpu(s)?\b", "data ?center", "inference", "serving",
           r"\bvllm\b", "tensorrt", "quantiz", "kv[- ]cache", "cluster",
           r"\bchips?\b", "accelerator"],
          "Growing",
          "Compute remains the bottleneck; efficiency (inference, quantization, "
          "specialized silicon) is where the next cost curve is won."),
]

BY_SLUG = {t.slug: t for t in TRENDS}


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _recent(conn: sqlite3.Connection, days: int) -> list[Article]:
    rows = conn.execute(
        """SELECT a.* FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE s.active = 1
                 AND COALESCE(a.published_at, a.fetched_at) >= datetime('now', ?)
           ORDER BY COALESCE(a.published_at, a.fetched_at) DESC""",
        (f"-{days} days",),
    ).fetchall()
    return [Article.from_row(r) for r in rows]


def _impact_tier(count: int) -> str:
    if count >= 25:
        return "Critical"
    if count >= 12:
        return "High"
    if count >= 4:
        return "Moderate"
    return "Low"


def compute_trends(conn: sqlite3.Connection) -> list[dict]:
    """One dict per trend with live volume/momentum + top stories, sorted by
    a blend of recent volume and momentum (most active first)."""
    arts = _recent(conn, 14)
    now = datetime.now(timezone.utc)
    wk1 = now - timedelta(days=7)

    out: list[dict] = []
    for t in TRENDS:
        matched = [a for a in arts if t.matches(a)]
        last7 = prev7 = 0
        for a in matched:
            dt = _parse(a.published_at or a.fetched_at)
            if dt is None:
                continue
            if dt >= wk1:
                last7 += 1
            else:
                prev7 += 1
        if prev7:
            momentum = round((last7 - prev7) / prev7 * 100)
        else:
            momentum = 100 if last7 else 0
        # clamp to a credible range — a freshly re-fetched corpus skews prev7 low
        momentum = max(-95, min(300, momentum))
        ranked = queries.dedupe_stories(queries.rank_articles(conn, matched))
        out.append({
            "trend": t,
            "count": len(matched),
            "last7": last7,
            "prev7": prev7,
            "momentum": momentum,
            "maturity": t.maturity,
            "impact": _impact_tier(len(matched)),
            "outlook": t.outlook,
            "top": ranked[:3],
        })
    out.sort(key=lambda d: (d["count"], d["momentum"]), reverse=True)
    return out


def radar(conn: sqlite3.Connection) -> list[dict]:
    """Compact trend rows for the Briefing 'AI Radar' viz (name, momentum, count,
    a 0-100 volume bar relative to the busiest trend)."""
    trends = compute_trends(conn)
    peak = max((d["count"] for d in trends), default=0) or 1
    return [{
        "name": d["trend"].name,
        "emoji": d["trend"].emoji,
        "slug": d["trend"].slug,
        "count": d["count"],
        "momentum": d["momentum"],
        "maturity": d["maturity"],
        "bar": round(d["count"] / peak * 100),
    } for d in trends]
