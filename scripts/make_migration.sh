#!/usr/bin/env bash
set -euo pipefail

# Generate a new Alembic migration with an autogenerate message.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

message="${1:-}"
if [[ -z "$message" ]]; then
    read -r -p "Migration message: " message
fi

if [[ -z "$message" ]]; then
    echo "Migration message is required." >&2
    exit 1
fi

cd "$repo_root"

echo "Creating Alembic revision: $message"
alembic revision --autogenerate -m "$message"
