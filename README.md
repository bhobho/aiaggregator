# aiaggregator

Local-first AI / Agentic-AI news dashboard. Runs entirely on your machine with **no paid APIs**:
RSS/Atom ingestion + local LLM enrichment via [Ollama](https://ollama.com) + SQLite + a
FastAPI/HTMX dashboard.

## Features

- Aggregates news from AI labs (OpenAI, Anthropic, Google DeepMind, Meta AI, Microsoft,
  AWS, Nvidia, Hugging Face) and major tech-news outlets (TechCrunch, The Verge, Ars
  Technica, VentureBeat, MIT Tech Review) plus a keyword-filtered Hacker News feed — all
  via RSS/Atom. Edit `feeds.yaml` to add or remove sources.
- Local LLM enrichment (default `qwen2.5:7b`): one-line summary, company/topic tags, and an
  importance score per article.
- Cross-source clustering: the same story from multiple outlets is grouped.
- Daily markdown digest of the top stories.
- Filter by company/category/time/importance, full-text search (SQLite FTS5), and a health
  view with a manual refresh button.

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) running locally with a model pulled:
  ```sh
  ollama pull qwen2.5:7b
  ```

## Run

```sh
uv sync
./run.sh            # serves http://localhost:8000
```

On first launch it creates the SQLite DB, loads `feeds.yaml`, and fetches once. Use the
**Refresh now** button on `/health` (or `POST /refresh`) to fetch on demand. A background
scheduler then fetches/enriches periodically and builds a daily digest.

## Configuration

Copy `.env.example` to `.env` to override defaults (Ollama host, model, DB path, refresh
interval). Edit `feeds.yaml` to add/remove sources.

## Tests

```sh
uv run pytest
```
