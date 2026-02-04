# Quote Tool

Quote Tool is a web app for quick freight quotes. It handles Hotshot and Air jobs for Freight Services staff and trusted partners.
Enter ZIP codes, weight, and extras to see a price, then escalate the booking through the internal email workflows or the Freight
Services portal.

## Features

- Sign up, log in, and reset passwords
- Global request throttling protects login and password reset endpoints
- Freight Services sign-ups using `@freightservices.net` emails are created as
  pending employees so administrators can approve their access
- Admin area to approve users, edit rates, and review the quote history
- Staff-only booking helpers let approved Freight Services employees email booking or volume-pricing requests directly from a quote
- Super admins can manage Office 365 SMTP credentials from the dashboard
- Price engine uses Google Maps and rate tables
- Quotes saved in a database
- Warns when shipment weight or cost exceeds tool limits

## Feature status

| Feature | Status | Notes |
| --- | --- | --- |
| Hotshot and Air quoting | ✅ Stable | Accepts form and JSON submissions and persists quotes. |
| Booking email workflow (`Email to Request Booking`) | 🔒 Staff-only | Restricted to approved employees or super admins whose email matches `MAIL_PRIVILEGED_DOMAIN`. Customers see the button disabled. |
| Volume-pricing email workflow | 🔒 Staff-only | Surfaces when a quote exceeds thresholds; limited to users with mail privileges. |
| Quote summary emailer | 🔒 Staff-only | Enabled for Freight Services staff only. Requires SMTP credentials and mail privileges. |
| Redis caching | ⚙️ Optional | Disabled by default. Enable with `COMPOSE_PROFILES=cache` and Redis configuration. |

## Documentation hub

- [Documentation Hub](docs/README.md) – Cross-reference of every guide, inline
  comment, and help topic.
- [Architecture](ARCHITECTURE.md) – Component breakdown and reimplementation
  notes for porting the app to another stack.
- [Deployment](DEPLOYMENT.md) – Production roll-out, TLS, and maintenance
  checklists.
- In-app Help Center (`/help`) – Task-oriented user guides rendered from
  `templates/help/`.

## Quick Start (local development)

1. Install Python 3.8 or newer.
2. Copy `.env.example` to `.env` and fill in keys and database info.
   - Generate a long random value for `SECRET_KEY` (for example,
     `python -c 'import secrets; print(secrets.token_urlsafe(32))'`) so sessions
     persist across restarts. Production deployments must set `SECRET_KEY`
     explicitly; otherwise the app starts in maintenance mode and reports a
     configuration error when `ENVIRONMENT` or `FLASK_ENV` is set to
     `production`. Without it in development the app falls back to an ephemeral
     key at startup.
   - If you use Docker Compose for local development, see
     [`docs/local_dev.md`](docs/local_dev.md) for the recommended setup and
     `POSTGRES_HOST` overrides when running helper scripts outside Docker.
3. Install packages: `pip install -r requirements-dev.txt`.
4. Create tables: `alembic upgrade head`.
5. Import ZIP and air rate data before starting the app:
   `python scripts/import_air_rates.py path/to/rates_dir`.
6. Seed rate tables and (optionally) create an admin user:
   `python init_db.py` (uses the directory above by default).
7. Run the app locally: `python flask_app.py`.
   - For production use
     `hypercorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --access-logfile - "app.app:create_app()"`
     (HTTP/2-capable when `h2` is installed) or the convenience launcher at
     `./scripts/start_gunicorn.sh` for a Gunicorn-based option
     (`PORT`, `GUNICORN_WORKERS`, and `GUNICORN_THREADS` tune concurrency).
   - Do not add a `--factory` flag to Gunicorn; the factory is invoked via the
     `()` syntax shown above, and Gunicorn does not recognize a `--factory`
     option.
   - `flask_app.py` is intended for local development only, so use the
     production entrypoints above for deployed environments.
   - The server reads ``FLASK_DEBUG`` (defaults to ``false``) to control
     debugging. Set ``FLASK_DEBUG=true`` while developing locally and leave it
     unset or ``false`` in production so the hardened configuration stays in
     effect.
8. On first run, visit `/setup` to confirm environment variables, optionally
   save missing values (including database connection settings) into the app's
   settings table, initialize the database schema, and create the initial super
   admin account. When required environment variables are missing, requests
   redirect to the setup checklist instead of a generic 500 page so operators
   can resolve the configuration. Database connection changes take effect after
   restarting the app. The setup flow locks down the rest of the app until a
   user exists.

### Database migrations

Generate new Alembic migrations with the helper script, which accepts a message
argument or prompts you interactively:

```bash
./scripts/make_migration.sh "describe change"
```

Run `alembic upgrade head` after creating a revision to apply it locally.

Alembic uses the app's `SQLALCHEMY_DATABASE_URI` setting when running online
migrations. If the URL contains percent-encoded values, the migration setup
escapes `%` so ConfigParser interpolation does not corrupt the URL.

### Docker (production-style container)

1. Build the container image:
   ```bash
   docker build -t quote-tool:latest .
   ```
2. Run the container, providing the environment settings from your `.env`
   (including database configuration and API keys):
   ```bash
   docker run --rm -p 8080:8080 --env-file .env quote-tool:latest
   ```

The container starts Gunicorn with the Flask application factory. It listens on
`$PORT` (defaults to `8080`) and requires the same environment variables as the
non-containerized deployment paths described above.

### Cloud Run + Cloud SQL

Cloud Run requires an external PostgreSQL database; the app starts in
maintenance mode and reports a configuration error in production without a
valid Postgres DSN. Configure Cloud SQL using one of these options:

- **TCP** – Provide `DATABASE_URL` with a PostgreSQL DSN (recommended) or set
  `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
  `POSTGRES_DB`. Add `POSTGRES_OPTIONS=sslmode=require` when connecting over a
  public IP.
- **Unix socket** – Set `CLOUD_SQL_CONNECTION_NAME` alongside
  `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`. Cloud Run mounts the
  socket at `/cloudsql/<connection-name>`, and the app builds the socket-based
  DSN automatically with the socket path preserved (no `%2F` encoding in the
  host query parameter). Optional `POSTGRES_OPTIONS` are appended as query
  parameters.

Use `./scripts/setup_gcp.sh PROJECT_ID` to bootstrap required Google Cloud
services, an Artifact Registry repo, and a Cloud SQL Postgres instance with
the `quote_tool` database. Set `REGION` (defaults to `us-central1`),
`CLOUD_SQL_INSTANCE_NAME`, `ARTIFACT_REPO_NAME`, or `POSTGRES_PASSWORD` to
override defaults before running the script.

For the Quote Tool Cloud SQL instance, configure either of the following:

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

Use a Cloud SQL **PostgreSQL** instance; MySQL is not supported by the
application's SQLAlchemy configuration.

If Cloud Run start-ups are timing out while waiting on database connectivity,
set `STARTUP_DB_CHECKS=false` to skip migrations and table inspection during
boot. This speeds container readiness but requires you to run migrations (for
example, `alembic upgrade head`) separately and validate connectivity via the
`/healthz` endpoint once the database is online.

When configuration validation fails, operators can opt into diagnostics by
setting `SHOW_CONFIG_ERRORS=true`. In non-production environments the app
exposes configuration error details in the 500 error page and via the
`/healthz/config` endpoint. Production deployments only expose this diagnostic
data when the explicit opt-in flag is enabled.

### Windows executable

Administrators who prefer a self-contained Windows build can package the app
with PyInstaller. The Dockerfile at `Dockerfile.windows` runs the following
command to produce `windows_setup.exe` and embed the rate CSV fixtures alongside
the launcher:

```powershell
pyinstaller --noconfirm --onefile --name windows_setup `
  --add-data ".env.example;." `
  --add-data "Hotshot_Rates.csv;rates" `
  --add-data "beyond_price.csv;rates" `
  --add-data "accessorial_cost.csv;rates" `
  --add-data "Zipcode_Zones.csv;rates" `
  --add-data "cost_zone_table.csv;rates" `
  --add-data "air_cost_zone.csv;rates" `
  windows_setup.py
```

Launching `windows_setup.exe` (or the copied `run_app.exe`) walks through a
guided configuration that collects:

- `ADMIN_EMAIL` for the initial administrator account.
- `ADMIN_PASSWORD`, entered securely with `getpass` so it is not echoed back.
- `GOOGLE_MAPS_API_KEY` used by address validation and quoting forms.
- `SECRET_KEY`, with the option to press Enter and let the launcher generate a
  new random key.

The answers are written to a `.env` file that lives beside the executable. Rerun
the prompts at any time with `windows_setup.exe --reconfigure`; leaving the
`SECRET_KEY` blank during reconfiguration rotates it to a freshly generated
value.

On first launch the executable seeds the database by invoking
[`init_db.initialize_database`](init_db.py#L82). The bundled rate data includes
`Hotshot_Rates.csv`, `beyond_price.csv`, `accessorial_cost.csv`,
`Zipcode_Zones.csv`, `cost_zone_table.csv`, and `air_cost_zone.csv`. Replace the
CSV files in the `rates` directory next to the executable (or its
`resources\rates` extraction folder when frozen) to load custom pricing the next
time you run the launcher.

Security guidance:

- Treat the generated `.env` as sensitive because it stores administrator
  credentials and API keys. Restrict NTFS permissions to the operations team and
  keep a copy in an enterprise secret vault rather than on shared drives.
- Rotate the Flask `SECRET_KEY` if the `.env` might have leaked. Run
  `windows_setup.exe --reconfigure` and press Enter when prompted for the key to
  create a new one.
- Update credentials recorded in `.env` (for example, `ADMIN_PASSWORD`) through
  your password manager, then rerun the launcher to synchronize the file.
- Review the docstring of
  [`init_db.initialize_database`](init_db.py#L82) for additional background on
  how seeding works and what tables are touched so you can plan migrations and
  audits accordingly.

### Testing

Run the automated test suite with [pytest](https://docs.pytest.org/) after you
install the development dependencies and configure your environment variables.
From the project root, execute:

```bash
pytest
```

This command discovers and runs all tests in the `tests/` directory. Use it to
verify changes before deploying or opening a pull request. Production builds
should continue to install only `requirements.txt`.

### Rate limiting

The application uses [Flask-Limiter](https://flask-limiter.readthedocs.io/) to
throttle abusive traffic. By default each client IP may perform up to 200
requests per day and 50 per hour, while the `/login` and `/reset` endpoints are
restricted to five POST attempts per minute for a given IP and email
combination. Override the defaults with environment variables as needed:

| Variable | Purpose | Default |
| --- | --- | --- |
| `RATELIMIT_DEFAULT` | Global limits applied to all routes | `200 per day;50 per hour` |
| `RATELIMIT_STORAGE_URI` | Backend storage for counters | `memory://` |
| `AUTH_LOGIN_RATE_LIMIT` | Per-user/IP throttle for `/login` | `5 per minute` |
| `AUTH_RESET_RATE_LIMIT` | Per-user/IP throttle for `/reset` | `5 per minute` |
| `AUTH_RESET_TOKEN_RATE_LIMIT` | Frequency cap for issuing password reset tokens | `1 per 15 minutes` |

Set `RATELIMIT_HEADERS_ENABLED=true` to expose standard rate-limit headers if
your proxy or monitoring stack expects them.

### Rate CSV formats

The `Zipcode_Zones.csv` file must include a header row with these columns in
order:

1. `Zipcode`
2. `Dest Zone`
3. `BEYOND`

Headers must match exactly; missing or transposed columns will cause the import
script to raise an error.

### Local development (Docker)
For local Docker and Docker Compose workflows, see
[`docs/local_dev.md`](docs/local_dev.md).

### Bulk seeding user accounts

Use `scripts/seed_users.py` when you need to load several accounts at once.
The repository root contains `users_seed_template.csv` with two example rows.
Copy the file, replace the placeholder values, and run the script:

```bash
python scripts/seed_users.py --file path/to/your_users.csv
```

Passwords must either satisfy the complexity rules enforced by
`services.auth_utils.is_valid_password` or be pre-hashed values generated by
`werkzeug.security.generate_password_hash`. Set `--update-existing` to modify
accounts that already exist and `--dry-run` to validate the CSV without writing
changes. The script automatically upgrades records flagged with
`is_admin=TRUE` to the `super_admin` role and ensures employee approvals align
with the selected role.

> ⚠️ Leave ``FLASK_DEBUG`` unset (the default) in production deployments. Turning
> it on exposes the Werkzeug debugger and prevents Gunicorn from running with
> the hardened configuration.

### Cloud Run ingress and TLS

Cloud Run terminates TLS for you and controls inbound access through ingress
settings. When you deploy the container image to Cloud Run, choose the ingress
mode that matches your access requirements:

1. **Public ingress** – Select **Allow all traffic** to expose the service
   publicly with a default `https://` URL.
2. **Private ingress** – Select **Internal** or **Internal and Cloud Load
   Balancing** if you want to restrict access to VPC-only traffic or a
   dedicated external load balancer.
3. **Custom domains and TLS** – Use Cloud Run's **Domain mappings** or an
   HTTPS load balancer to attach your hostname. Google-managed certificates
   handle TLS issuance and renewal automatically.

#### Configure environment variables

Set production configuration in Cloud Run (or through a CI/CD system that
targets Cloud Run). Store secrets in Secret Manager and reference them in the
service configuration:

```dotenv
FLASK_DEBUG=false
GOOGLE_MAPS_API_KEY=your_google_key
DATABASE_URL=postgresql+psycopg2://user:password@db/quote_tool
# Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'
SECRET_KEY=super_secret_value
# Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'
API_AUTH_TOKEN=replace_with_api_token
# Optional: override the default API rate limit (e.g., "30 per minute")
API_QUOTE_RATE_LIMIT=30 per minute
```

When targeting an external PostgreSQL instance (for example, Google Cloud SQL)
leave ``DATABASE_URL`` unset and provide the individual ``POSTGRES_*`` values
instead. The configuration helper automatically percent-encodes credentials so
passwords containing characters such as ``?`` or ``@`` do not break the
connection string. Optional ``POSTGRES_OPTIONS`` accepts a query-string-style
value that is appended to the generated URI, enabling flags like
``sslmode=require`` without manually editing the DSN:

```dotenv
POSTGRES_USER=quote_tool
POSTGRES_PASSWORD=ChangeMeSuperSecret!
POSTGRES_DB=quote_tool
POSTGRES_HOST=34.132.95.126
POSTGRES_PORT=5432
POSTGRES_OPTIONS=sslmode=require&application_name=quote-tool
```

> Replace the example password with your real secret. Because
> ``POSTGRES_PASSWORD`` is encoded automatically, special characters do not need
> manual escaping.

PostgreSQL configuration is required in all environments. If no PostgreSQL
settings are supplied, the application starts in maintenance mode and surfaces
a configuration error. Provide `DATABASE_URL` or the `POSTGRES_*` environment
variables before launching the service.

#### Cloud Run TLS guidance

Cloud Run provisions managed TLS certificates automatically. For custom
domains, map the domain in Cloud Run or point an external HTTPS load balancer
at the service. The load balancer configuration also lets you layer on Cloud
Armor policies or IAP if you need additional access control beyond Cloud Run's
ingress settings.

## Advanced

- Run only the JSON API: `python standalone_flask_app.py`.
- Import rate data any time with the same script as above.
- Admin pages let you manage rate tables and fuel surcharges.

### JSON API authentication

The JSON API requires an API token in the `Authorization` header for every
request. Configure the token with `API_AUTH_TOKEN` and provide it as a bearer
token:

```bash
curl -H "Authorization: Bearer ${API_AUTH_TOKEN}" \
  http://localhost:5000/api/quote
```

Rate limiting for `/api/quote` endpoints is controlled with
`API_QUOTE_RATE_LIMIT` (defaults to `30 per minute`). Adjust the limiter
storage backend using `RATELIMIT_STORAGE_URI` if you need shared counters
across multiple workers.

## Troubleshooting

If you see `no such table` errors, the database is missing required tables.
Confirm the PostgreSQL connection details are correct, then run
`alembic upgrade head` to apply migrations. If you are initializing a fresh
environment, set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env` (the script loads
this file automatically), then run `python init_db.py` to seed the schema.

If quotes warn that "Air rate table(s) missing or empty", ensure the CSV
directory is present or specify its path via `RATE_DATA_DIR` before initializing
the database.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for an overview of the application's components and guidance on rebuilding the app in another stack.
