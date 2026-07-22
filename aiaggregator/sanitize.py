"""Minimal allowlist HTML sanitizer for rendering feed `content:encoded`.

Full post bodies (from the owner's own Medium feed) are rendered in-portal, so the
HTML is passed through a strict allowlist first: only a small set of structural tags
and a couple of safe attributes survive; scripts/styles/iframes and their contents are
dropped, and href/src are restricted to http(s)/relative/mailto. Uses only the stdlib.
"""
from __future__ import annotations

import html as _html
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "h4", "strong", "em", "b", "i", "u", "a",
    "ul", "ol", "li", "blockquote", "pre", "code", "figure", "figcaption",
    "img", "hr",
}
VOID_TAGS = {"br", "img", "hr"}
ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt"}}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "svg",
                     "noscript", "form", "input", "button"}


def _safe_url(url: str | None) -> str:
    url = (url or "").strip()
    low = url.lower()
    if low.startswith(("http://", "https://", "mailto:")) or url.startswith("/"):
        return url
    return ""  # drop javascript:, data:, vbscript:, etc.


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0  # depth inside a drop-with-content element

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        allowed = ALLOWED_ATTRS.get(tag, set())
        parts = [tag]
        for k, v in attrs:
            if k in allowed:
                if k in ("href", "src"):
                    v = _safe_url(v)
                    if not v:
                        continue
                parts.append(f'{k}="{_html.escape(v or "", quote=True)}"')
        if tag == "a":
            parts.append('target="_blank" rel="noopener nofollow"')
        self.out.append("<" + " ".join(parts) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)  # void tags emit without a closing tag

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            if self.skip:
                self.skip -= 1
            return
        if self.skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(_html.escape(data))


def clean(raw_html: str | None) -> str:
    """Return a sanitized HTML fragment safe to render, or '' if there's nothing."""
    if not raw_html:
        return ""
    p = _Sanitizer()
    p.feed(raw_html)
    p.close()
    return "".join(p.out).strip()
