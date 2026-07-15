"""Stock quotes for the ticker bar: AI companies trading on Nasdaq.

Uses Yahoo Finance's public chart endpoint (no API key). Quotes are cached
in-process for a few minutes so the bar never hammers the API.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

# (symbol, display name) — Nasdaq-listed AI / AI-infrastructure names.
TICKERS: list[tuple[str, str]] = [
    ("NVDA", "Nvidia"),
    ("MSFT", "Microsoft"),
    ("GOOGL", "Alphabet"),
    ("META", "Meta"),
    ("AMZN", "Amazon"),
    ("AAPL", "Apple"),
    ("AMD", "AMD"),
    ("AVGO", "Broadcom"),
    ("INTC", "Intel"),
    ("ARM", "Arm"),
    ("MU", "Micron"),
    ("PLTR", "Palantir"),
    ("TSLA", "Tesla"),
]

_TTL = 300.0  # seconds
_cache: dict = {"ts": 0.0, "data": []}


async def _fetch_one(client: httpx.AsyncClient, symbol: str, name: str) -> dict | None:
    r = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": "1d", "range": "1d"},
    )
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None or not prev:
        return None
    return {
        "symbol": symbol,
        "name": name,
        "price": round(price, 2),
        "change_pct": round((price - prev) / prev * 100, 2),
    }


async def quotes() -> list[dict]:
    """Cached quotes for all tickers. Returns stale/empty data on failure."""
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < _TTL:
        return _cache["data"]
    headers = {"User-Agent": "Mozilla/5.0 (aiaggregator ticker)"}
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            results = await asyncio.gather(
                *(_fetch_one(client, s, n) for s, n in TICKERS),
                return_exceptions=True,
            )
    except httpx.HTTPError as exc:
        log.warning("ticker fetch failed: %s", exc)
        return _cache["data"]
    data = [r for r in results if isinstance(r, dict)]
    if data:
        _cache.update(ts=now, data=data)
    return _cache["data"]
