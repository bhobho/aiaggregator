"""Minimal markdown -> HTML for the digest (only the subset we generate)."""
from __future__ import annotations

import html
import re

_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"\*([^*]+)\*")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    text = html.escape(text)
    text = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _CODE.sub(r'<code>\1</code>', text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return text


def md_to_html(md: str) -> str:
    out: list[str] = []
    for line in (md or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("### "):
            out.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{_inline(s[2:])}</h1>")
        else:
            out.append(f"<p>{_inline(s)}</p>")
    return "\n".join(out)
