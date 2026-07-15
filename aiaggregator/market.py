"""Market News categories and the logic to assign articles to them.

An article belongs to a market category when a category pattern matches its title
(strong signal) or its summary/tags (weaker fallback). Articles can belong to
multiple categories. Sources listed in feeds.yaml with `category: market` carry a
name hint so their items always land in the intended bucket even before enrichment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Article


@dataclass(frozen=True)
class MarketCategory:
    slug: str
    name: str
    initials: str
    bg: str        # tile accent background (ramp 50)
    fg: str        # text on accent (ramp 900)
    blurb: str     # one-line description shown on the tile / detail page
    pattern: str   # regex matched against title (strong) and summary/tags (fallback)


CATEGORIES: list[MarketCategory] = [
    MarketCategory(
        "finance", "AI Finance", "Fi", "#E1F5EE", "#04342C",
        "funding, investments, revenue, valuations, IPOs",
        r"funding|rais(es|ed|ing)\b|investment|investor|valuation|revenue|\bipo\b|"
        r"series [a-f]\b|venture capital|\bvc\b|earnings|market cap|"
        r"\$\d+(\.\d+)? ?(billion|million|bn|b\b|m\b)|stock (price|surge|jump)",
    ),
    MarketCategory(
        "regulation", "AI Regulation", "Re", "#E6F1FB", "#042C53",
        "laws, government policy, compliance, privacy, antitrust",
        r"regulat|\blaw(s|maker)?\b|legislation|\bbill\b|ai act|executive order|"
        r"policy|compliance|privacy|gdpr|antitrust|\bftc\b|\bdoj\b|congress|senate|"
        r"white house|governance|copyright|lawsuit|court rul|\bban(s|ned)?\b",
    ),
    MarketCategory(
        "ma", "AI Acquisitions & Mergers", "MA", "#FAECE7", "#4A1B0C",
        "M&A, acquisitions, strategic investments",
        r"acqui(res?|red)\b|(?<!talent )(?<!customer )(?<!user )(?<!data )acquisition|"
        r"merger|\bm&a\b|takeover|\bbuys\b|buyout|"
        r"strategic (investment|stake)|majority stake|absorbs",
    ),
    MarketCategory(
        "partnerships", "AI Partnerships", "Pa", "#FAEEDA", "#412402",
        "alliances, collaborations, joint ventures",
        r"partner(s|ship|ing)?\b|teams? up|collaborat|alliance|joint venture|"
        r"signs? (a )?deal|multi[- ]year (deal|agreement)|integrates? with",
    ),
    MarketCategory(
        "infrastructure", "AI Infrastructure", "In", "#EAF3DE", "#173404",
        "chips, cloud, data centers, compute",
        r"\bchips?\b|semiconductor|\bgpus?\b|\btpus?\b|data ?cent(er|re)s?|"
        r"\bcloud\b|compute|supercomputer|\bfoundry\b|\btsmc\b|\bfab\b|"
        r"energy|\bpower (grid|plant|deal)|nuclear|megawatt|gigawatt",
    ),
    MarketCategory(
        "enterprise", "AI Enterprise Adoption", "En", "#E6F1FB", "#042C53",
        "how businesses deploy and use AI",
        r"enterprise|adoption|deploys?\b|workplace|workforce|productivity|"
        r"\bcios?\b|\bceos?\b say|business(es)? (use|adopt)|roll(s|ing)? out .{0,20}(company|employees)|"
        r"automation of (work|jobs)|transform(s|ing)? (business|industry)",
    ),
    MarketCategory(
        "security", "AI Security & Safety", "Se", "#FCEBEB", "#501313",
        "vulnerabilities, misuse, alignment, deepfakes",
        r"security|\bsafety\b|vulnerab|jailbreak|deepfake|misuse|cyberattack|"
        r"\bbreach\b|guardrail|alignment|red[- ]team|\bscams?\b|fraud|"
        r"malware|phishing|exploit",
    ),
    MarketCategory(
        "startups", "AI Startups", "St", "#F1EFE8", "#2C2C2A",
        "new companies, founders, seed rounds, unicorns",
        r"startups?\b|founders?\b|seed (round|funding)|y combinator|unicorn|"
        r"stealth (mode|startup)|co[- ]founder|spin[- ]?off|new venture",
    ),
]

BY_SLUG: dict[str, MarketCategory] = {c.slug: c for c in CATEGORIES}

# feeds.yaml market sources → the bucket their items always belong to
# (keyword matching still applies on top, so items can join other buckets too).
SOURCE_HINTS: dict[str, str] = {
    "AI Funding & Investment": "finance",
    "AI Regulation & Policy": "regulation",
    "AI Mergers & Acquisitions": "ma",
    "AI Partnerships": "partnerships",
    "AI Infrastructure": "infrastructure",
    "AI Enterprise Adoption": "enterprise",
    "AI Security & Safety": "security",
    "AI Startup News": "startups",
}

_RES: dict[str, re.Pattern] = {c.slug: re.compile(c.pattern, re.I) for c in CATEGORIES}


def categories_for(article: Article, source_name: str | None = None) -> list[MarketCategory]:
    """All market categories an article belongs to."""
    title = article.title or ""
    body = (article.summary or article.raw_summary or "") + " " + " ".join(article.tags)
    hinted = SOURCE_HINTS.get(source_name or "")
    out = []
    for c in CATEGORIES:
        if c.slug == hinted or _RES[c.slug].search(title) or _RES[c.slug].search(body):
            out.append(c)
    return out
