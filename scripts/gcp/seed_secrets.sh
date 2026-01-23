#!/usr/bin/env bash
set -euo pipefail

# Seed Google Secret Manager with Quote Tool secrets.
#
# Inputs (required):
#   PROJECT_ID           GCP project ID to store secrets in.
#   POSTGRES_PASSWORD    Database password consumed by the app.
#   MAIL_PASSWORD        SMTP password or app password.
#   GOOGLE_MAPS_API_KEY  Google Maps API key used for distance lookups.
#
# Example usage:
#   export PROJECT_ID="my-project"
#   export POSTGRES_PASSWORD="super-secret"
#   export MAIL_PASSWORD="smtp-secret"
#   export GOOGLE_MAPS_API_KEY="maps-secret"
#   ./scripts/gcp/seed_secrets.sh

: "${PROJECT_ID:?Set PROJECT_ID to your GCP project ID.}"
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD to the database password.}"
: "${MAIL_PASSWORD:?Set MAIL_PASSWORD to the SMTP password.}"
: "${GOOGLE_MAPS_API_KEY:?Set GOOGLE_MAPS_API_KEY to the Maps API key.}"

ensure_secret() {
    local secret_name="$1"
    local secret_value="$2"

    if ! gcloud secrets describe "${secret_name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
        gcloud secrets create "${secret_name}" \
            --project "${PROJECT_ID}" \
            --replication-policy="automatic"
    fi

    printf "%s" "${secret_value}" | gcloud secrets versions add "${secret_name}" \
        --project "${PROJECT_ID}" \
        --data-file=-
}

ensure_secret "POSTGRES_PASSWORD" "${POSTGRES_PASSWORD}"
ensure_secret "MAIL_PASSWORD" "${MAIL_PASSWORD}"
ensure_secret "GOOGLE_MAPS_API_KEY" "${GOOGLE_MAPS_API_KEY}"

echo "Secrets uploaded to project ${PROJECT_ID}."
