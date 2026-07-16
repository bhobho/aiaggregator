"""Title normalization shared by de-duplication and clustering.

Google News (and many outlets) append " - Outlet Name" to headlines, so the
same story arrives with different titles per source. Stripping that suffix
before comparing/clustering lets one story collapse into one entry.
"""
from __future__ import annotations

import html
import re

_DASHES = r"[-–—]"  # hyphen, en dash, em dash
_SUFFIX_RE = re.compile(rf"\s+{_DASHES}\s+[^-–—]{{2,48}}$")
# suffixes that are content, not an outlet name ("… - Part 2", "… - Chapter 1")
_CONTENT_SUFFIX_RE = re.compile(r"(?i)^(part|episode|ep\.?|chapter|vol\.?|volume|q[1-4]|day|week|year)\b")


def strip_outlet_suffix(title: str) -> str:
    """Drop a trailing ' - Outlet Name' from a headline, if present."""
    m = _SUFFIX_RE.search(title or "")
    if not m:
        return title or ""
    suffix = m.group(0).strip().lstrip("-–— ").strip()
    if _CONTENT_SUFFIX_RE.match(suffix):
        return title
    return title[: m.start()]


def outlet_of(title: str) -> str:
    """The trailing ' - Outlet Name' publisher from a headline, or '' if none.
    Google News titles carry the source outlet here (the link is a redirect)."""
    m = _SUFFIX_RE.search(title or "")
    if not m:
        return ""
    suffix = m.group(0).strip().lstrip("-–— ").strip()
    if _CONTENT_SUFFIX_RE.match(suffix):
        return ""
    return suffix


def normalize_title(title: str) -> str:
    """Canonical form for duplicate detection: outlet suffix removed,
    entities unescaped, lowercased, punctuation collapsed."""
    t = html.unescape(title or "")
    t = strip_outlet_suffix(t).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()
