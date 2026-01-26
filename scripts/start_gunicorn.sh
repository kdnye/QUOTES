#!/usr/bin/env bash
# Start the Quote Tool with Gunicorn using configurable worker counts.
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
    --bind "0.0.0.0:${port}" \
    "app:create_app()"
