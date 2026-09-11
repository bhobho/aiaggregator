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
    fetch_interval: int = 43200  # fetch feeds every 12 hours
    enrich_interval: int = 60    # drain enrichment queue every 60 s
    enrich_batch: int = 8        # articles per enrichment pass
    image_backfill_interval: int = 45  # drain missing-thumbnail queue every 45 s
    image_backfill_batch: int = 20     # articles per image-backfill pass
    # Interval is a floor, not a cadence: passes run sequentially (one Ollama
    # call at a time) and each pass takes as long as it takes, so a large batch
    # with a short interval just means the next pass starts the moment the
    # previous one finishes instead of sitting idle.
    detail_backfill_interval: int = 15  # drain missing-long-summary queue every 15 s
    detail_backfill_batch: int = 20     # articles per detail-summary backfill pass (LLM calls)

    # Visitor geolocation (see analytics.resolve_pending) — ip-api.com's free tier
    # caps at 45 requests/min from our server's IP, so this runs as its own slow
    # background pass rather than inline on an /_insights page load: batch *
    # (1 request per ~1.5s pacing, see analytics._lookup) must stay well under
    # that per run, and the interval must be longer than a batch takes to run.
    geo_resolve_interval: int = 90  # drain missing-geo queue every 90 s
    geo_resolve_batch: int = 40     # visitor IPs resolved per pass

    # Public base URL (e.g. https://news.example.com) used to build absolute
    # Open Graph URLs for link previews. Leave empty to derive from the request.
    public_url: str = ""

    # Hidden analytics page (not linked in the UI).
    # If analytics_token is set, the page requires ?key=<token>; otherwise it is
    # reachable at analytics_path by obscurity only. Set the token in .env since the
    # repo is public.
    analytics_path: str = "/_insights"
    analytics_token: str = ""
    # Comma-separated IPs to exclude from all visit/engagement tracking and
    # reporting (e.g. the site owner's own IP, so self-visits don't skew stats).
    # The insights page shows the IP the server sees you as, to make this easy
    # to fill in. Example: AIAGG_ANALYTICS_EXCLUDE_IPS=203.0.113.7,198.51.100.4
    analytics_exclude_ips: str = ""

    @property
    def analytics_exclude_ip_set(self) -> set[str]:
        return {ip.strip() for ip in self.analytics_exclude_ips.split(",") if ip.strip()}

    # Hidden blog editor (not linked in the UI) — same obscurity + token pattern
    # as the analytics page above. Set the token in .env since the repo is public;
    # with no token set the page 404s for everyone (safe default).
    editor_path: str = "/_editor"
    editor_token: str = ""

    @property
    def uploads_dir(self) -> Path:
        """Where editor-uploaded images live — under data/, not static/, so
        personal/uploaded images never end up committed to the (public) repo."""
        return self.db_path.parent / "uploads"

    # Ingestion
    http_timeout: float = 30.0
    user_agent: str = "aiaggregator/0.1 (+local; RSS reader)"
    max_items_per_feed: int = 50

    # Clustering
    cluster_window_days: int = 7
    cluster_threshold: float = 0.42  # TF-IDF cosine (fallback path)
    embed_cluster_threshold: float = 0.80  # title-embedding cosine to merge stories

    # Composite ranking (weights need not sum to 1; relative scale matters)
    rank_w_importance: float = 0.35     # LLM-assigned significance
    rank_w_recency: float = 0.30        # how fresh
    rank_w_priority: float = 0.55       # match to prioritized themes (models/agents/infra/tools)
    rank_w_cluster: float = 0.15        # how many sources cover the story
    rank_w_source: float = 0.10         # source-type trust
    rank_w_announcement: float = 0.25   # generic launch/release cue
    rank_recency_halflife_hours: float = 30.0


settings = Settings()
