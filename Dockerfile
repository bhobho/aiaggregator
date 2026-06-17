# syntax=docker/dockerfile:1

# ---- Build stage: resolve deps from the locked manifest with uv ----
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cached) using only the lockfile + manifest, so the
# layer is reused across source-only changes. --no-dev skips pillow/pytest.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Then install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Runtime stage: slim image, no uv, just the prebuilt venv + source ----
FROM python:3.14-slim-bookworm

WORKDIR /app

# Run as an unprivileged user; create the data dir it can write to.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app

COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AIAGG_DB_PATH=/app/data/aiaggregator.db \
    AIAGG_OLLAMA_HOST=http://host.docker.internal:11434

USER app
EXPOSE 9000

# --proxy-headers lets the app build correct https:// URLs behind a reverse proxy.
CMD ["uvicorn", "aiaggregator.main:app", \
     "--host", "0.0.0.0", "--port", "9000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
