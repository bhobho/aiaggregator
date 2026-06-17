"""Curated enterprise vendors and the logic to assign articles to them.

An article belongs to a vendor if the vendor's company name appears in the article's
LLM-extracted companies or its source company, OR a vendor keyword appears in the title
(fallback for not-yet-enriched items). Articles can belong to multiple vendors.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Article


@dataclass(frozen=True)
class Vendor:
    slug: str
    name: str
    initials: str
    bg: str       # tile accent background (ramp 50)
    fg: str       # text on accent (ramp 900)
    name_keys: tuple[str, ...]    # matched against companies + source company
    title_terms: tuple[str, ...]  # substrings matched in the title (fallback)


VENDORS: list[Vendor] = [
    Vendor("openai", "OpenAI", "Op", "#E1F5EE", "#04342C",
           ("openai",), ("openai", "chatgpt", "gpt-5", "gpt-4", "codex")),
    Vendor("anthropic", "Anthropic", "An", "#FAECE7", "#4A1B0C",
           ("anthropic",), ("anthropic", "claude")),
    Vendor("google", "Google / Gemini", "Go", "#E6F1FB", "#042C53",
           ("google", "google deepmind"), ("gemini", "deepmind", "google ai")),
    Vendor("microsoft", "Microsoft", "Ms", "#EEEDFE", "#26215C",
           ("microsoft",), ("microsoft", "copilot")),
    Vendor("aws", "AWS", "aw", "#FAEEDA", "#412402",
           ("aws", "amazon"), ("aws ", "amazon bedrock", "sagemaker")),
    Vendor("nvidia", "NVIDIA", "Nv", "#EAF3DE", "#173404",
           ("nvidia",), ("nvidia",)),
    Vendor("meta", "Meta", "Me", "#E6F1FB", "#042C53",
           ("meta",), ("llama",)),
    Vendor("deepseek", "DeepSeek", "De", "#FBEAF0", "#4B1528",
           ("deepseek",), ("deepseek",)),
    Vendor("perplexity", "Perplexity", "Pe", "#E1F5EE", "#04342C",
           ("perplexity",), ("perplexity",)),
    Vendor("moonshot", "Kimi / Moonshot", "Ki", "#FAECE7", "#4A1B0C",
           ("moonshot",), ("kimi", "moonshot")),
    Vendor("huggingface", "Hugging Face", "Hf", "#FAEEDA", "#412402",
           ("hugging face",), ("hugging face", "huggingface")),
    Vendor("mistral", "Mistral", "Mi", "#FCEBEB", "#501313",
           ("mistral",), ("mistral",)),
    Vendor("xai", "xAI / Grok", "xA", "#F1EFE8", "#2C2C2A",
           ("xai",), ("grok", "xai")),
]

BY_SLUG: dict[str, Vendor] = {v.slug: v for v in VENDORS}


def matches(v: Vendor, companies_lower: set[str], title_lower: str) -> bool:
    if companies_lower & set(v.name_keys):
        return True
    return any(term in title_lower for term in v.title_terms)


def vendors_for(article: Article, source_company: str | None) -> list[Vendor]:
    companies = {c.lower() for c in article.companies}
    if source_company:
        companies.add(source_company.lower())
    title = (article.title or "").lower()
    return [v for v in VENDORS if matches(v, companies, title)]
