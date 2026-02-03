#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 PROJECT_ID" >&2
    exit 1
}

if [[ $# -lt 1 ]] || [[ -z "${1:-}" ]]; then
    usage
fi

PROJECT_ID="$1"
REGION="${REGION:-us-central1}"
REPO_NAME="${ARTIFACT_REPO_NAME:-quote-repo}"
INSTANCE_NAME="${CLOUD_SQL_INSTANCE_NAME:-quote-postgres}"
DB_NAME="quote_tool"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    # Uses Python's secrets module to generate a strong password.
    POSTGRES_PASSWORD="$(python - <<'PY'
import secrets

print(secrets.token_urlsafe(18))
PY
)"
    GENERATED_PASSWORD="true"
else
    GENERATED_PASSWORD="false"
fi

# Enable required Google Cloud APIs using `gcloud services enable`.
gcloud services enable run.googleapis.com sqladmin.googleapis.com compute.googleapis.com \
    --project="${PROJECT_ID}"

# Create the Artifact Registry repository (Docker format) if missing.
if ! gcloud artifacts repositories describe "${REPO_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" >/dev/null 2>&1; then
    gcloud artifacts repositories create "${REPO_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --repository-format="docker" \
        --description="Container images for Quote Tool"
fi

# Create the Cloud SQL Postgres instance if missing.
if ! gcloud sql instances describe "${INSTANCE_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud sql instances create "${INSTANCE_NAME}" \
        --project="${PROJECT_ID}" \
        --database-version="POSTGRES_14" \
        --tier="db-f1-micro" \
        --region="${REGION}" \
        --root-password="${POSTGRES_PASSWORD}"
fi

# Create the application database if missing.
if ! gcloud sql databases describe "${DB_NAME}" \
    --instance="${INSTANCE_NAME}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud sql databases create "${DB_NAME}" \
        --instance="${INSTANCE_NAME}" \
        --project="${PROJECT_ID}"
fi

CONNECTION_NAME="$(gcloud sql instances describe "${INSTANCE_NAME}" \
    --project="${PROJECT_ID}" \
    --format="value(connectionName)")"

if [[ "${GENERATED_PASSWORD}" == "true" ]]; then
    echo "Generated Cloud SQL postgres password (store securely): ${POSTGRES_PASSWORD}" >&2
fi

echo "Cloud SQL connection name: ${CONNECTION_NAME}"
