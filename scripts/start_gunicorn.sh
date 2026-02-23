#!/usr/bin/env bash
# Start the Quote Tool with Gunicorn using configurable worker counts.
#
# Environment variables:
#   GUNICORN_WORKERS: Number of worker processes (default: 1)
#   GUNICORN_THREADS: Threads per worker (default: 8)
#   GUNICORN_TIMEOUT: Worker timeout in seconds (default: 0 for Cloud Run)
#   PORT: Port to bind the HTTP server (default: 8080)
#
# Example:
#   PORT=8080 GUNICORN_WORKERS=5 GUNICORN_THREADS=8 ./scripts/start_gunicorn.sh

set -euo pipefail

workers=${GUNICORN_WORKERS:-1}
threads=${GUNICORN_THREADS:-8}
timeout=${GUNICORN_TIMEOUT:-0}
port=${PORT:-8080}
exec gunicorn \
    -w "${workers}" \
    -k gthread \
    --threads "${threads}" \
    --timeout "${timeout}" \
    --bind ":${port}" \
    "app.app:create_app()"
