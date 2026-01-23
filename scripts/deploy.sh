#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REGION="${REGION:-us-central1}"
DEFAULT_SERVICE_NAME="${SERVICE_NAME:-quote-tool}"
DEFAULT_POSTGRES_USER="${POSTGRES_USER:-quote_tool}"
DEFAULT_POSTGRES_DB="${POSTGRES_DB:-quote_tool}"

require_command() {
    local command_name="$1"

    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "Error: '${command_name}' is required but was not found in PATH." >&2
        exit 1
    fi
}

prompt_required() {
    local prompt_label="$1"
    local input=""

    while true; do
        read -r -p "${prompt_label}: " input
        input="${input//\r/}"
        if [[ -n "${input}" ]]; then
            echo "${input}"
            return
        fi
        echo "${prompt_label} cannot be empty. Please try again." >&2
    done
}

prompt_required_secret() {
    local prompt_label="$1"
    local input=""

    while true; do
        read -r -s -p "${prompt_label}: " input
        echo >&2
        input="${input//\r/}"
        if [[ -n "${input}" ]]; then
            echo "${input}"
            return
        fi
        echo "${prompt_label} cannot be empty. Please try again." >&2
    done
}

prompt_optional_secret() {
    local prompt_label="$1"
    local input=""

    read -r -s -p "${prompt_label}: " input
    echo >&2
    input="${input//\r/}"
    echo "${input}"
}

prompt_with_default() {
    local prompt_label="$1"
    local default_value="$2"
    local input=""

    read -r -p "${prompt_label} [${default_value}]: " input
    input="${input//\r/}"
    if [[ -z "${input}" ]]; then
        echo "${default_value}"
        return
    fi
    echo "${input}"
}

prompt_with_optional_default() {
    local prompt_label="$1"
    local default_value="$2"
    local input=""

    if [[ -n "${default_value}" ]]; then
        read -r -p "${prompt_label} [${default_value}]: " input
        input="${input//\r/}"
        if [[ -z "${input}" ]]; then
            echo "${default_value}"
            return
        fi
        echo "${input}"
        return
    fi

    read -r -p "${prompt_label}: " input
    input="${input//\r/}"
    echo "${input}"
}

require_no_commas() {
    local label="$1"
    local value="$2"

    if [[ "${value}" == *","* ]]; then
        echo "Error: ${label} cannot include commas when passed to --set-env-vars." >&2
        exit 1
    fi
}

require_command gcloud
require_command python

PROJECT_ID="${PROJECT_ID:-}"
if [[ -z "${PROJECT_ID}" ]]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null | tr -d '\r')"
fi
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
    PROJECT_ID="$(prompt_required "Google Cloud project ID")"
fi

REGION="$(prompt_with_default "Cloud Run region" "${DEFAULT_REGION}")"
SERVICE_NAME="$(prompt_with_default "Cloud Run service name" "${DEFAULT_SERVICE_NAME}")"

IMAGE_URI="${IMAGE_URI:-}"
if [[ -z "${IMAGE_URI}" ]]; then
    IMAGE_URI="$(gcloud run services describe "${SERVICE_NAME}" \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --format="value(spec.template.spec.containers[0].image)" 2>/dev/null || true)"
fi

IMAGE_URI="$(prompt_with_optional_default "Container image URI" "${IMAGE_URI}")"
if [[ -z "${IMAGE_URI}" ]]; then
    echo "Error: Container image URI is required." >&2
    exit 1
fi

CLOUD_SQL_CONNECTION_NAME="${CLOUD_SQL_CONNECTION_NAME:-}"
if [[ -z "${CLOUD_SQL_CONNECTION_NAME}" ]]; then
    mapfile -t connection_candidates < <(gcloud sql instances list \
        --project="${PROJECT_ID}" \
        --format="value(connectionName)" 2>/dev/null | sed '/^$/d')

    if [[ ${#connection_candidates[@]} -eq 1 ]]; then
        CLOUD_SQL_CONNECTION_NAME="${connection_candidates[0]}"
    elif [[ ${#connection_candidates[@]} -gt 1 ]]; then
        echo "Available Cloud SQL instances:" >&2
        for index in "${!connection_candidates[@]}"; do
            printf "  %d) %s\n" "$((index + 1))" "${connection_candidates[$index]}" >&2
        done
        while true; do
            selection="$(prompt_required "Select Cloud SQL instance number")"
            if [[ "${selection}" =~ ^[0-9]+$ ]] \
                && ((selection >= 1)) \
                && ((selection <= ${#connection_candidates[@]})); then
                CLOUD_SQL_CONNECTION_NAME="${connection_candidates[$((selection - 1))]}"
                break
            fi
            echo "Invalid selection. Please choose a number from the list." >&2
        done
    fi
fi

if [[ -z "${CLOUD_SQL_CONNECTION_NAME}" ]]; then
    CLOUD_SQL_CONNECTION_NAME="$(prompt_required "Cloud SQL connection name (project:region:instance)")"
fi

POSTGRES_USER="$(prompt_with_default "Postgres user" "${DEFAULT_POSTGRES_USER}")"
POSTGRES_DB="$(prompt_with_default "Postgres database" "${DEFAULT_POSTGRES_DB}")"
DB_PASSWORD="$(prompt_required_secret "DB_PASSWORD")"

SECRET_KEY="$(prompt_optional_secret "SECRET_KEY (leave blank to generate)")"
if [[ -z "${SECRET_KEY}" ]]; then
    SECRET_KEY="$(python - <<'PY'
import secrets

print(secrets.token_urlsafe(32))
PY
)"
    echo "Generated SECRET_KEY for deployment." >&2
fi

MAPS_KEY="$(prompt_required "MAPS_KEY")"

require_no_commas "DB_PASSWORD" "${DB_PASSWORD}"
require_no_commas "SECRET_KEY" "${SECRET_KEY}"
require_no_commas "MAPS_KEY" "${MAPS_KEY}"
require_no_commas "POSTGRES_USER" "${POSTGRES_USER}"
require_no_commas "POSTGRES_DB" "${POSTGRES_DB}"
require_no_commas "CLOUD_SQL_CONNECTION_NAME" "${CLOUD_SQL_CONNECTION_NAME}"
require_no_commas "IMAGE_URI" "${IMAGE_URI}"
require_no_commas "SERVICE_NAME" "${SERVICE_NAME}"
require_no_commas "PROJECT_ID" "${PROJECT_ID}"
require_no_commas "REGION" "${REGION}"

gcloud run deploy "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --image="${IMAGE_URI}" \
    --set-env-vars="DB_PASSWORD=${DB_PASSWORD},POSTGRES_PASSWORD=${DB_PASSWORD},POSTGRES_USER=${POSTGRES_USER},POSTGRES_DB=${POSTGRES_DB},CLOUD_SQL_CONNECTION_NAME=${CLOUD_SQL_CONNECTION_NAME},SECRET_KEY=${SECRET_KEY},MAPS_KEY=${MAPS_KEY},GOOGLE_MAPS_API_KEY=${MAPS_KEY},FLASK_APP=app:create_app,BRANDING_STORAGE=gcs"
