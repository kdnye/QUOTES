#!/bin/bash
#
# Startup script for the Quote Tool application stack.
#
# This script brings up the required services (Redis and PostgreSQL),
# runs database migrations and seeds, and starts the Flask app in
# detached mode. It assumes you have a `.env` file with the
# necessary environment variables in the repository root.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR" || exit 1

# Ensure .env file exists
if [[ ! -f .env ]]; then
  echo "Error: .env file not found in $SCRIPT_DIR." >&2
  exit 1
fi

# Load environment variables from .env.  We intentionally do not set
# IFS to avoid trimming whitespace in values, and use `export` to
# propagate them to child processes.
set -a
source .env
set +a

# Ensure the compose files exist
COMPOSE_FILES="-f docker-compose.yml -f compose.local-postgres.yml"
for f in docker-compose.yml compose.local-postgres.yml; do
  if [[ ! -f $f ]]; then
    echo "Error: Required compose file '$f' not found in $SCRIPT_DIR." >&2
    exit 1
  fi
done

# Create data directories with proper ownership/permissions
echo "Creating and setting permissions on data directories…"
sudo install -d -m 0700 -o 999 -g 999 ./data/postgres
sudo install -d -m 0750 -o "$(id -u)" -g "$(id -g)" ./data/redis ./instance ./rates

# Start Redis and PostgreSQL services in detached mode
echo "Starting infrastructure services (redis, postgres)…"
docker compose $COMPOSE_FILES up -d redis postgres

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to become ready…"
until docker compose $COMPOSE_FILES exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
  sleep 2
done
echo "PostgreSQL is ready."

# Construct the internal database URL for migrations
DBURL_NET="postgresql+psycopg2://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB"

# Run database migrations (alembic upgrade head)
echo "Running database migrations…"
docker compose $COMPOSE_FILES run --rm \
  -e DATABASE_URL="$DBURL_NET" \
  -e SQLALCHEMY_DATABASE_URI="$DBURL_NET" \
  quote_tool alembic upgrade head

# Seed the database
echo "Seeding database…"
docker compose $COMPOSE_FILES run --rm \
  -e DATABASE_URL="$DBURL_NET" \
  -e SQLALCHEMY_DATABASE_URI="$DBURL_NET" \
  quote_tool python init_db.py

# Start the application service in detached mode
echo "Starting the quote_tool service…"
docker compose $COMPOSE_FILES up -d quote_tool

echo "Quote Tool is starting. It should be accessible on http://localhost:5000 or http://localhost:8000 (if mapped)."
echo "You can verify its status with: docker compose $COMPOSE_FILES ps"

echo "Startup sequence complete."
