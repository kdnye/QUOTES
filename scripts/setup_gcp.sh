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
BUCKET_NAME="${PROJECT_ID}-branding"
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

# Create the GCS bucket (or ensure it exists) with uniform bucket-level access.
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --uniform-bucket-level-access
fi

# Enforce uniform bucket-level access and allow public read of objects.
gcloud storage buckets update "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --member="allUsers" \
    --role="roles/storage.objectViewer"

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
echo "Bucket name: gs://${BUCKET_NAME}"
