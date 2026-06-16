#!/usr/bin/env bash
# Launch the aiaggregator dashboard locally.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run uvicorn aiaggregator.main:app --host 127.0.0.1 --port 8000 "$@"
