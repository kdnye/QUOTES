# Deployment Guide

This document describes how to promote the Quote Tool application into a
production environment on Cloud Run backed by Cloud SQL. It expands on the
quick-start notes in `README.md` and is intended for operations staff who need
precise, repeatable steps to stand up the service. For local development
workflows (including Docker Compose), see
[docs/local_dev.md](docs/local_dev.md). For a cross-reference of the full
documentation set, consult [docs/README.md](docs/README.md).

## 1. Prerequisites

1. **Cloud project access** – A Google Cloud project with billing enabled and
   permissions to manage Cloud Run, Cloud SQL, Artifact Registry, and IAM.
2. **Container build tooling** – Use Cloud Build or a local Docker Engine 24+
   install to build and push container images.
3. **Domain name and DNS** – Decide on the hostname you will publish and
   create the matching DNS record in your managed DNS provider. Cloud Run
   domain mappings and HTTPS load balancers both require the hostname to point
   at Google-managed endpoints before TLS can be provisioned.
4. **Ingress and TLS strategy** – Choose whether Cloud Run will be public
   (**Allow all traffic**) or limited to VPC/private traffic (**Internal** or
   **Internal and Cloud Load Balancing**). Cloud Run and Google-managed HTTPS
   load balancers automatically issue and renew TLS certificates for custom
   domains.
5. **External services** – Provision the dependencies used by the Quote Tool:
   - **Database** – Cloud SQL for PostgreSQL 13+ is required. The application
     depends on PostgreSQL in all environments, including local development.
   - **Google Maps API access** – Enable the Distance Matrix API and obtain an
     API key.
   - **Redis (optional)** – Use a managed Redis provider (for example, Cloud
     Memorystore) when `CACHE_TYPE` points at Redis.
   - **SMTP relay (optional)** – Needed for password reset emails. Configure the
     `MAIL_*` settings if you enable this feature.

## 2. Fetch the application

Clone the repository (or download the release archive) onto the deployment
host:

```bash
sudo mkdir -p /opt/quote_tool
sudo chown "$USER" /opt/quote_tool
cd /opt/quote_tool
git clone https://example.com/your-fork.git .
```

If you maintain a private fork, update the URL accordingly. Verify the working
tree is clean before continuing:

```bash
git status -sb
```

## 3. Configure environment variables

Configure environment variables in Cloud Run (or a CI/CD system that deploys to
Cloud Run). Store secrets in Secret Manager and reference them in the service
configuration instead of committing a `.env` file. Generate a deployment secret
with `python -c 'import secrets; print(secrets.token_urlsafe(32))'` and paste
the output into `SECRET_KEY` so the Flask app can reuse it across restarts.
Production deployments must define `SECRET_KEY` (otherwise the app starts in
maintenance mode and reports a configuration error when `ENVIRONMENT` or
`FLASK_ENV` is set to `production`). Provide either a Cloud SQL connection name
or a full PostgreSQL DSN (`DATABASE_URL`) so the container can connect to Cloud
SQL. The `scripts/deploy.sh` workflow now creates or updates Secret Manager
entries named `${SERVICE_NAME}-db-password`, `${SERVICE_NAME}-secret-key`, and
`${SERVICE_NAME}-maps-key` before it deploys the Cloud Run service, so ensure
the operator has the required permissions to manage secrets.

```dotenv
# Core application settings
# Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'
SECRET_KEY=replace-with-long-random-string
GOOGLE_MAPS_API_KEY=your-google-key
FLASK_DEBUG=false
TZ=UTC
POSTGRES_USER=quote_tool
POSTGRES_PASSWORD=strong_password
POSTGRES_DB=quote_tool
POSTGRES_HOST=cloudsql-hostname
# DATABASE_URL=postgresql+psycopg2://override_user:override_password@override-host:5432/override_db

# Optional tuning (uncomment as needed)
# DB_POOL_SIZE=10
# CACHE_TYPE=redis
# CACHE_REDIS_URL=redis://redis-host:6379/0
# RATELIMIT_STORAGE_URI=redis://redis-host:6379/1
# MAIL_DEFAULT_SENDER=quote@freightservices.net
# MAIL_SERVER=smtp.gmail.com
# MAIL_PORT=587
# MAIL_USE_TLS=true
# MAIL_USERNAME=quote@freightservices.net
# MAIL_PASSWORD=app-password
# MAIL_ALLOWED_SENDER_DOMAIN=freightservices.net
# MAIL_PRIVILEGED_DOMAIN=freightservices.net
# MAIL_RATE_LIMIT_PER_RECIPIENT_PER_DAY=25
# MAIL_RATE_LIMIT_PER_USER_PER_HOUR=10
# MAIL_RATE_LIMIT_PER_USER_PER_DAY=50
# MAIL_RATE_LIMIT_PER_FEATURE_PER_HOUR=200
# RATE_DATA_DIR=/app/rates  # Custom directory for CSV imports
# ADMIN_EMAIL=admin@example.com
# ADMIN_PASSWORD=initial-password
```

When configuring Gmail SMTP, use `smtp.gmail.com` with port 587 and
`MAIL_USE_TLS=true`. Gmail requires either OAuth 2.0 or an app password for
accounts with two-step verification enabled; basic username/password sign-in
without app passwords is no longer supported for most accounts. If you prefer
SSL on port 465, set `MAIL_USE_SSL=true` and disable TLS.

### Seed secrets in Secret Manager

Store sensitive values in Secret Manager and reference them from Cloud Run
instead of committing them to `.env` files. The script below wraps the standard
`gcloud secrets create` and `gcloud secrets versions add` commands and creates
secrets for the database password, SMTP password, and Google Maps API key:

```bash
export PROJECT_ID="your-project-id"
export POSTGRES_PASSWORD="replace-with-db-password"
export MAIL_PASSWORD="replace-with-mail-password"
export GOOGLE_MAPS_API_KEY="replace-with-maps-key"
./scripts/gcp/seed_secrets.sh
```

If you prefer manual commands, the equivalent `gcloud` calls are:

```bash
gcloud secrets create POSTGRES_PASSWORD --project=PROJECT --replication-policy=automatic
printf "%s" "replace-with-db-password" | \\
  gcloud secrets versions add POSTGRES_PASSWORD --project=PROJECT --data-file=-

gcloud secrets create MAIL_PASSWORD --project=PROJECT --replication-policy=automatic
printf "%s" "replace-with-mail-password" | \\
  gcloud secrets versions add MAIL_PASSWORD --project=PROJECT --data-file=-

gcloud secrets create GOOGLE_MAPS_API_KEY --project=PROJECT --replication-policy=automatic
printf "%s" "replace-with-maps-key" | \\
  gcloud secrets versions add GOOGLE_MAPS_API_KEY --project=PROJECT --data-file=-
```

### Bind secrets to Cloud Run

Bind Secret Manager values at deploy time with `--set-secrets`. Map the secret
name to the environment variable expected by the app (for example, the
`POSTGRES_PASSWORD` secret is injected as `POSTGRES_PASSWORD` in the container):

```bash
gcloud run deploy quote-tool \\
  --image=LOCATION-docker.pkg.dev/PROJECT/REPO/quote-tool:TAG \\
  --region=REGION \\
  --platform=managed \\
  --allow-unauthenticated \\
  --set-env-vars=FLASK_DEBUG=false \\
  --set-secrets=SECRET_KEY=projects/PROJECT/secrets/SECRET_KEY:latest,\\
POSTGRES_PASSWORD=projects/PROJECT/secrets/POSTGRES_PASSWORD:latest,\\
MAIL_PASSWORD=projects/PROJECT/secrets/MAIL_PASSWORD:latest,\\
GOOGLE_MAPS_API_KEY=projects/PROJECT/secrets/GOOGLE_MAPS_API_KEY:latest
```

When using `scripts/deploy.sh`, the deployment operator needs permissions to
describe, create, and add new versions to secrets. Grant `roles/secretmanager.admin`
or a least-privilege combination that includes `secretmanager.secrets.create`,
`secretmanager.secrets.get`, and `secretmanager.versions.add` on the project or
on the specific secrets.

### Cloud Run runtime IAM permissions

Ensure the Cloud Run runtime service account has the minimum IAM permissions
required to read secrets and reach dependencies:

- `roles/secretmanager.secretAccessor` on the secrets above so Cloud Run can
  inject values at startup.
- `roles/cloudsql.client` if the service connects to Cloud SQL.

### Variable reference

| Variable | Required | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | Yes | Secures Flask sessions and CSRF tokens. Required in production when `ENVIRONMENT` or `FLASK_ENV` is set to `production`; otherwise the app starts in maintenance mode and reports a configuration error. Generate at least 32 random bytes (see `python -c 'import secrets; print(secrets.token_urlsafe(32))'`). |
| `FLASK_DEBUG` | Yes | Set to `false` in production to disable Flask's interactive debugger and reloader. |
| `GOOGLE_MAPS_API_KEY` | Yes | Authenticates calls to Google’s Distance Matrix API. |
| `TZ` | Yes | Time zone used for timestamp formatting and log rotation. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Yes | Credentials and database name consumed by Cloud SQL and the Flask app's default connection string when `DATABASE_URL` is not set. |
| `POSTGRES_HOST` | No | Hostname or IP for Cloud SQL when using TCP connections. |
| `CLOUD_SQL_CONNECTION_NAME` | No | Cloud SQL instance connection name used to build a Unix socket DSN on Cloud Run (for example `project:region:instance`). Requires the standard `POSTGRES_*` credentials. |
| `DATABASE_URL` | No | Override the default connection string with a PostgreSQL DSN when using an external database. |
| `DB_POOL_SIZE` | No | Overrides the SQLAlchemy connection pool size when using PostgreSQL or MySQL. |
| `CACHE_TYPE` / `CACHE_REDIS_URL` | No | Configure Flask-Caching. Leave unset to disable caching or point to your managed Redis instance. |
| `RATELIMIT_STORAGE_URI` | No | Storage backend for Flask-Limiter counters. Defaults to `memory://` unless you set it to a Redis instance. |
| `MAIL_*` | No | Configure outbound email (SMTP host, credentials, TLS/SSL flags). Needed for password resets. Super admins can also supply these values through the **Admin → Mail Settings** page at runtime. |
| `MAIL_ALLOWED_SENDER_DOMAIN` | No | Restricts `MAIL_DEFAULT_SENDER` to an approved sender domain (defaults to `freightservices.net`). |
| `MAIL_PRIVILEGED_DOMAIN` | No | Controls which user email domains can access advanced mail features. |
| `MAIL_RATE_LIMIT_PER_*` | No | Tune per-user, per-feature, and per-recipient rate limits for outbound email traffic. |
| `RATE_DATA_DIR` | No | Directory containing the rate CSV files consumed by `init_db.py`. Defaults to the repository root. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | No | When set, `init_db.py` bootstraps an administrator account with these credentials. |
| `HEALTHCHECK_REQUIRE_DB` | No | Set to a truthy value to require database connectivity for the `/healthz` probe endpoint. |
| `HEALTHCHECK_DB_TIMEOUT_SECONDS` | No | Timeout (in seconds) for the optional database probe performed by `/healthz`. Defaults to `2.0`. |

Never commit the `.env` file to version control. Restrict filesystem
permissions (`chmod 600 .env`) so only the deployment user can read it.

### Optional services and feature gating

- **SMTP-dependent workflows** – Booking and volume-pricing email helpers, along with the quote summary emailer, require
  outbound email credentials. Without `MAIL_*` values or runtime overrides, those buttons remain disabled for all users.
- **Staff-only email features** – Even with SMTP enabled, only users whose email domain matches `MAIL_PRIVILEGED_DOMAIN` and who
  are approved employees or super admins can access the booking and volume email forms. Customer accounts will see the controls
  in a disabled state.
- **Redis caching** – Disabled unless you explicitly set `CACHE_TYPE`. Confirm Redis credentials are reachable before enabling it.

## 4. Prepare Cloud SQL

Provision a Cloud SQL PostgreSQL instance and note the instance connection name
(`project:region:instance`). Ensure the Cloud Run service account has
permission to connect (`roles/cloudsql.client`) and that you have credentials
ready for `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

If you plan to connect over TCP (public or private IP), also capture the host
and port so you can set `POSTGRES_HOST` and `POSTGRES_PORT` (or build a
`DATABASE_URL`).

## 5. Build and deploy the container

Use Cloud Build or a local Docker build to publish your container image to
Artifact Registry or another registry reachable by Cloud Run. Example using
Cloud Build with the provided `cloudbuild.yaml` (which builds, pushes, and
deploys to Cloud Run in one pipeline):

```bash
gcloud builds submit --config=cloudbuild.yaml \\
  --substitutions=_IMAGE_URI=LOCATION-docker.pkg.dev/PROJECT/REPO/quote-tool:TAG,_SERVICE=quote-tool,_REGION=REGION \\
  .
```

If you need a manual deploy (for example, to change the authentication
settings), you can deploy the image to Cloud Run directly (set environment
variables via `--set-env-vars` and secrets via `--set-secrets` or in the Cloud
Console):

```bash
gcloud run deploy quote-tool \\
  --image=LOCATION-docker.pkg.dev/PROJECT/REPO/quote-tool:TAG \\
  --region=REGION \\
  --platform=managed \\
  --allow-unauthenticated \\
  --set-env-vars=FLASK_DEBUG=false,GOOGLE_MAPS_API_KEY=YOUR_KEY \\
  --set-secrets=SECRET_KEY=projects/PROJECT/secrets/SECRET_KEY:latest
```

For an interactive workflow, `scripts/deploy.sh` prompts for the required
Cloud Run environment values, infers the active project and existing service
image when possible, and deploys with the Cloud SQL connection values needed
by the application.

### Cloud Run entrypoint

The container image defaults to binding on `0.0.0.0:${PORT:-8080}` using
Hypercorn's application factory callable (HTTP/2-capable when `h2` is installed).
For Cloud Run, set the entrypoint to:

```bash
hypercorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --access-logfile - "app.app:create_app()"
```

This entrypoint targets `app.app:create_app` and respects the platform-provided
`PORT` value. Avoid passing `--factory` here; Gunicorn does not support that
flag, and the `()` call syntax is the compatible way to invoke the factory
function. Keep `flask_app.py` for local development only, as the production
deployment uses the factory-based Hypercorn entrypoint above.

### Cloud Build logging requirements (service accounts)

If your Cloud Build trigger specifies a custom `build.service_account`, Cloud
Build requires explicit log handling. Configure one of the supported logging
strategies or the trigger fails with an `invalid argument` error. Choose the
option that matches your security and retention needs:

- **Log bucket** – Set `build.logs_bucket` to a user-owned Cloud Storage bucket
  for build logs.
- **Regional user-owned bucket** – Set
  `build.options.default_logs_bucket_behavior` to
  `REGIONAL_USER_OWNED_BUCKET`.
- **Cloud Logging only** – Set `build.options.logging` to
  `CLOUD_LOGGING_ONLY` (or `NONE` to disable logging).

The repository now includes a ready-to-use `cloudbuild.yaml` that builds,
pushes, and deploys the container image while enabling Cloud Logging only by
default. Update the `_IMAGE_URI`, `_SERVICE`, and `_REGION` substitutions if you
want to publish to a different registry, tag format, or Cloud Run destination.
The pipeline defaults to `--allow-unauthenticated`; change that to
`--no-allow-unauthenticated` if you want to require IAM authentication.

Example `cloudbuild.yaml` fragment using Cloud Logging only:

```yaml
options:
  logging: CLOUD_LOGGING_ONLY
```

Example `gcloud` command that sets the logging behavior on a trigger:

```bash
gcloud builds triggers update TRIGGER_NAME \
  --region=REGION \
  --build-config=cloudbuild.yaml \
  --logging=CLOUD_LOGGING_ONLY
```

Review [Cloud Build logging options](https://cloud.google.com/build/docs/securing-builds/store-manage-build-logs)
if you need to route logs to a dedicated bucket for compliance or retention.

### Cloud Run probes

Configure Cloud Run health checks to target the `/healthz` endpoint. The route
returns `200 OK` with a plain-text `ok` body by default. If you want readiness
and liveness probes to fail when the database is unavailable, set
`HEALTHCHECK_REQUIRE_DB=true` and optionally tune
`HEALTHCHECK_DB_TIMEOUT_SECONDS` (defaults to `2.0`) so the probe times out
quickly.

### Cloud Run database configuration

Cloud Run deployments must use an external PostgreSQL database. The application
will refuse to start in production if it cannot detect a valid PostgreSQL DSN.
Choose one of the following Cloud SQL connection styles:

- **Cloud SQL TCP (public/private IP)** – Configure a PostgreSQL `DATABASE_URL`
  (recommended) or set `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, and `POSTGRES_DB`. When using Cloud SQL public IP, add
  `POSTGRES_OPTIONS=sslmode=require` so the driver negotiates TLS.
- **Cloud SQL Unix socket (Cloud Run)** – Set
  `CLOUD_SQL_CONNECTION_NAME` alongside `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, and `POSTGRES_DB`. The application builds a socket-based
  DSN that targets `/cloudsql/<connection-name>`, matching the mount injected
  by the Cloud SQL Auth Proxy on Cloud Run. Optional `POSTGRES_OPTIONS` are
  appended as query parameters.

For the Quote Tool Cloud SQL instance, use the following values:

```bash
# Public IP (TCP)
POSTGRES_HOST=104.198.53.60
POSTGRES_PORT=5432
POSTGRES_OPTIONS=sslmode=require
```

```bash
# Cloud Run Unix socket
CLOUD_SQL_CONNECTION_NAME=quote-tool-483716:us-central1:quotetool-postgres-instance
```

For Cloud SQL, ensure the instance is running **PostgreSQL**, not MySQL, because
the application relies on PostgreSQL-specific drivers and migrations.

### Apply database migrations

Run Alembic migrations using a Cloud Run job or a temporary container that has
network access to Cloud SQL. One approach is to create a Cloud Run job that runs
the same image and executes `alembic upgrade head` with the same environment
variables you set for the service.

### Enable Redis caching and shared rate limiting (optional)

Point `CACHE_TYPE`, `CACHE_REDIS_URL`, and `RATELIMIT_STORAGE_URI` at your
managed Redis endpoint. When using Memorystore or another private Redis service,
ensure the Cloud Run service connects over VPC and that the Redis instance
allows traffic from the Cloud Run connector.

### Seed rate tables and admin user

The `init_db.py` helper imports base rate data and optionally creates an initial
administrator. Run it once after migrations finish:

Run the script in a Cloud Run job or a temporary container that can reach Cloud
SQL. Use the same environment variables (`RATE_DATA_DIR`, `ADMIN_EMAIL`,
`ADMIN_PASSWORD`) you configured for the service.

If `RATE_DATA_DIR` is defined, the script reads CSV files from that directory.
Otherwise it falls back to the CSVs checked into the repository root. Provide
`ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env` (or export them temporarily) to
bootstrap the first admin account.

## 6. Maintenance

### Backups

Enable automated Cloud SQL backups and export snapshots before upgrades or
schema changes. Store backups in a secured Cloud Storage bucket or your
organization’s backup system so you can roll back if an upgrade introduces
regressions.

### Upgrades

1. Back up the database using the command above (or your enterprise backup
   solution).
2. Build and publish a new container image (Cloud Build or local Docker).
3. Deploy the new image to Cloud Run.
4. Apply database migrations with the Cloud Run job described earlier.

Review the official PostgreSQL release notes for breaking changes, especially
when the underlying major version bumps. Major upgrades may require running
`pg_upgrade` manually or restoring from dump files.

## 7. Cloud Run ingress and TLS

Use Cloud Run or a Google-managed HTTPS load balancer to terminate TLS and
control inbound access:

1. **Ingress settings** – Choose **Allow all traffic** for public access or
   **Internal** / **Internal and Cloud Load Balancing** for private endpoints.
2. **Custom domains** – Use Cloud Run domain mappings for direct custom domain
   support, or place a HTTPS load balancer in front of the service for more
   advanced routing, Cloud Armor policies, or IAP.
3. **TLS certificates** – Google-managed certificates are provisioned and
   renewed automatically once DNS records point at the Cloud Run or load
   balancer endpoint.

## 8. Health checks and smoke tests

After the service is running, perform a quick validation:

```bash
curl -Ik https://quotes.example.com/
```

A successful response returns `HTTP/1.1 200 OK` (or a redirect to `/login`).
Log in with the administrator credentials created earlier and verify you can
request a quote.

## 9. Routine maintenance

- **Deploying updates** – Build and deploy a new container image, then run
  migrations via a Cloud Run job.
- **Backups** – Keep Cloud SQL backups and Cloud Run configuration exports (or
  infrastructure-as-code) alongside any migration runbooks.
- **Monitoring** – Stream Cloud Run logs to Cloud Logging or your preferred
  SIEM. Probe the `/healthz` endpoint for liveness/readiness checks.

## 10. Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `sqlalchemy.exc.OperationalError` on startup | Confirm the database host, port, and credentials in `DATABASE_URL`. Ensure outbound firewall rules permit traffic to the DB. |
| Cloud Run returns `403` or `404` | Verify the service ingress setting, IAM policy, and domain mapping status. Ensure the DNS record points at the Cloud Run or load balancer target. |
| Distance calculations fail | Validate `GOOGLE_MAPS_API_KEY` has the Distance Matrix API enabled and the server’s IP is authorized. |
| Password reset emails do not send | Set the `MAIL_*` environment variables and confirm the SMTP relay allows connections from Cloud Run. |

## 10. Security considerations

- Rotate the `SECRET_KEY` and database credentials if you suspect compromise.
  Changing `SECRET_KEY` will invalidate active sessions.
- Restrict Cloud Run IAM access to trusted administrators. Use VPC firewall
  rules and Cloud SQL authorized networks as needed.
- Keep base images patched. Rebuild the image after applying dependency updates
  in `requirements.txt`. Use `requirements-dev.txt` only for local development
  workflows and CI tooling.

Following this runbook yields a reproducible deployment of Quote Tool with HTTPS
termination, seeded data, and a hardened configuration ready for production.
