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
scheduler then fetches/enriches periodically.

## Run with Docker

```sh
docker compose up --build      # serves http://localhost:9000
```

The compose stack runs the FastAPI app on port 9000, persists the SQLite DB in a named
volume (`aiagg-data`), and mounts `feeds.yaml` read-only so you can edit sources without
rebuilding. Ollama is expected on the **host**: the container reaches it via
`http://host.docker.internal:11434` by default (works on Docker Desktop and, via the
`host-gateway` mapping, on Linux). Point at a remote Ollama with:

```sh
AIAGG_OLLAMA_HOST=http://my-ollama:11434 docker compose up
```

Any other `AIAGG_*` overrides in a local `.env` are picked up automatically.

## Configuration

Copy `.env.example` to `.env` to override defaults (Ollama host, model, DB path, refresh
interval). Edit `feeds.yaml` to add/remove sources.

## Tests

```sh
uv run pytest
```
