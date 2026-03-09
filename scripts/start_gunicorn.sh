#!/usr/bin/env bash
# Start the Quote Tool with Gunicorn using threaded workers and
# conservative connection lifecycle settings.
#
# Runtime behavior:
#   - Worker model: gthread
#   - Request timeout: 60 seconds
#   - Keep-alive timeout: 5 seconds
#
# Environment variables:
#   GUNICORN_WORKERS: Number of worker processes (default: 3)
#   GUNICORN_THREADS: Threads per worker (default: 4)
#   PORT: Port to bind the HTTP server (default: 8080)
#
# Example:
#   PORT=8080 GUNICORN_WORKERS=5 GUNICORN_THREADS=8 ./scripts/start_gunicorn.sh

set -euo pipefail

workers=${GUNICORN_WORKERS:-3}
threads=${GUNICORN_THREADS:-4}
port=${PORT:-8080}
exec gunicorn \
    -w "${workers}" \
    -k gthread \
    --threads "${threads}" \
    --timeout 60 \
    --keep-alive 5 \
    --bind "0.0.0.0:${port}" \
    "app.app:create_app()"
