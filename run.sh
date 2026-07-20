#!/usr/bin/env bash
# Launch the aiaggregator dashboard locally.
set -euo pipefail
cd "$(dirname "$0")"
# --proxy-headers + forwarded-allow-ips let the app build correct https:// absolute
# URLs (for Open Graph link previews) when run behind Cloudflare Tunnel / a reverse proxy.
exec uv run uvicorn aiaggregator.main:app --host 127.0.0.1 --port 9002 \
    --proxy-headers --forwarded-allow-ips="*" "$@"
