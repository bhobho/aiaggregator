"""Thin async client for the local Ollama HTTP API (no paid API)."""
from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import settings

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


# Small local models (e.g. qwen2.5:7b) sometimes wrap the requested JSON object
# in a sentence, a markdown code fence, or trailing commentary despite
# "format": "json" — more often on messy input (emoji-heavy YouTube
# descriptions, promo links) than on plain article text. Rather than hard-fail
# the whole enrichment on that, fall back to pulling out the first balanced
# {...} block before giving up.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_response(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(raw)
        if not match:
            raise
        return json.loads(match.group(0))


async def is_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_host}/api/tags")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


async def list_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_host}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except httpx.HTTPError:
        return []


async def generate_json(prompt: str, *, system: str | None = None,
                        model: str | None = None, retries: int = 1) -> dict:
    """Call /api/generate with JSON format; parse and return the object."""
    payload = {
        "model": model or settings.ollama_model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
    }
    if system:
        payload["system"] = system

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
                r = await client.post(f"{settings.ollama_host}/api/generate", json=payload)
                r.raise_for_status()
                raw = r.json().get("response", "")
                return _parse_json_response(raw)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_exc = exc
            log.warning("ollama generate attempt %d failed: %s", attempt + 1, exc)
    raise OllamaError(str(last_exc))


async def embed(text: str, *, model: str | None = None) -> list[float] | None:
    """Return an embedding vector, or None if the embed model isn't available."""
    payload = {"model": model or settings.ollama_embed_model, "prompt": text}
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
            r = await client.post(f"{settings.ollama_host}/api/embeddings", json=payload)
            r.raise_for_status()
            return r.json().get("embedding")
    except httpx.HTTPError:
        return None
