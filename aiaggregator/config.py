"""Application configuration via environment / .env."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AIAGG_", extra="ignore")

    # Storage
    db_path: Path = BASE_DIR / "data" / "aiaggregator.db"
    feeds_path: Path = BASE_DIR / "feeds.yaml"

    # Ollama (local, no paid API)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text"  # used only if available
    ollama_timeout: float = 120.0

    # Scheduling (seconds)
    fetch_interval: int = 1800   # fetch feeds every 30 min
    enrich_interval: int = 60    # drain enrichment queue every 60 s
    enrich_batch: int = 8        # articles per enrichment pass
    digest_hour: int = 7         # build daily digest at 07:00 local

    # Public base URL (e.g. https://news.example.com) used to build absolute
    # Open Graph URLs for link previews. Leave empty to derive from the request.
    public_url: str = ""

    # Hidden analytics page (not linked in the UI).
    # If analytics_token is set, the page requires ?key=<token>; otherwise it is
    # reachable at analytics_path by obscurity only. Set the token in .env since the
    # repo is public.
    analytics_path: str = "/_insights"
    analytics_token: str = ""

    # Ingestion
    http_timeout: float = 30.0
    user_agent: str = "aiaggregator/0.1 (+local; RSS reader)"
    max_items_per_feed: int = 50

    # Clustering
    cluster_window_days: int = 7
    cluster_threshold: float = 0.42  # cosine similarity to merge near-duplicate stories

    # Composite ranking (weights need not sum to 1; relative scale matters)
    rank_w_importance: float = 0.40     # LLM-assigned significance
    rank_w_recency: float = 0.30        # how fresh
    rank_w_cluster: float = 0.15        # how many sources cover the story
    rank_w_source: float = 0.10         # source-type trust
    rank_w_announcement: float = 0.40   # product / framework / agentic announcement
    rank_recency_halflife_hours: float = 30.0


settings = Settings()
