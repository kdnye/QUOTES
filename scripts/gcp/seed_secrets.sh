#!/usr/bin/env bash
set -euo pipefail

# Seed Google Secret Manager with Quote Tool secrets.
#
# Inputs (required):
#   PROJECT_ID           GCP project ID to store secrets in.
#   POSTGRES_PASSWORD    Database password consumed by the app.
#   POSTMARK_SERVER_API_TOKEN  Postmark Server API token used for SMTP auth.
#   GOOGLE_MAPS_API_KEY  Google Maps API key used for distance lookups.
#
# Example usage:
#   export PROJECT_ID="my-project"
#   export POSTGRES_PASSWORD="super-secret"
#   export POSTMARK_SERVER_API_TOKEN="postmark-server-token"
#   export GOOGLE_MAPS_API_KEY="maps-secret"
#   ./scripts/gcp/seed_secrets.sh

: "${PROJECT_ID:?Set PROJECT_ID to your GCP project ID.}"
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD to the database password.}"
: "${POSTMARK_SERVER_API_TOKEN:?Set POSTMARK_SERVER_API_TOKEN to the Postmark Server API token.}"
: "${GOOGLE_MAPS_API_KEY:?Set GOOGLE_MAPS_API_KEY to the Maps API key.}"

ensure_secret() {
    local secret_name="$1"
    local secret_value="$2"
    # Inputs: secret_name (Secret Manager secret name), secret_value (secret
    # payload). Output: creates the secret and adds a new version. External
    # dependency: calls `gcloud secrets` commands to manage Secret Manager data.

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
ensure_secret "POSTMARK_SERVER_API_TOKEN" "${POSTMARK_SERVER_API_TOKEN}"
ensure_secret "GOOGLE_MAPS_API_KEY" "${GOOGLE_MAPS_API_KEY}"

echo "Secrets uploaded to project ${PROJECT_ID}."
