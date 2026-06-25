# Quote Tool

Quote Tool is a web app for quick freight quotes. It handles Hotshot and Air jobs for Freight Services staff and trusted partners.
Enter ZIP codes, weight, and extras to see a price, then escalate the booking through the internal email workflows or the Freight
Services portal.

## Features

- Sign up, log in, and reset passwords
- After login, users are routed to a rate-set-specific landing page (see
  [Post-login landing pages](#post-login-landing-pages)). Users tagged with the
  `scicr` / `science_care` rate set land on `/sc/quote`; everyone else lands on
  `/quotes/new`.
- Global request throttling protects login and password reset endpoints
- Freight Services sign-ups using `@freightservices.net` emails are created as
  pending employees so administrators can approve their access
- Admin area to approve users, edit rates, and review the quote history
- Staff-only booking helpers let approved Freight Services employees email booking or volume-pricing requests directly from a quote
- Science Care multi-leg quotes (`/sc/quote`) stamp a unified **multi-leg reference** (auto-assigned `SCMQNNNN` or customer-supplied) across every leg, expose an aggregated **Email Ops for Booking** action that ships the full multi-leg summary to operations **without** the $15 booking fee that the single-quote workflow applies, and offer an SC-scoped **multi-leg lookup** (`/sc/quote/lookup`) so any SC user can pull up a prior multi-leg quote by its reference number.
- Super admins can manage Office 365 SMTP credentials from the dashboard
- Price engine uses Google Maps and rate tables
- "Create New Quote" validates origin and destination ZIP codes against Google Places when a Maps key is configured
- "Create New Quote" defaults to Air mode unless the user explicitly selects Hotshot
- Quotes saved in a database
- Air shipment validation blocks quotes when billable pounds per piece exceeds 300 lbs
- Threshold warnings appear when Air billable weight exceeds 1200 lbs, or when any quote exceeds 3000 lbs total weight or $6000 total cost
- Theme now supports dark mode and automatically follows each user's system color-scheme preference
- Dynamic fuel surcharges (VSC) are calculated per **destination** zone using weekly EIA regional diesel prices; surcharge percentages are looked up from a tiered matrix (16%–28%) and applied automatically to both Hotshot and Air quotes. Air rates and the destination-VSC source mirror the FSI Shipping Quote Tool 2026 VSC-Locked workbook (the company's authoritative rate card).

## Feature status

| Feature | Status | Notes |
| --- | --- | --- |
| Hotshot and Air quoting | ✅ Stable | Accepts form and JSON submissions and persists quotes. Both pricing paths mirror the FSI Shipping Quote Tool 2026 VSC-Locked workbook (Air rates from `Domestic Air Quotes!C4:E11`; Hotshot rates from `Domestic Hotshot Quotes!E45:G54` with the weight-break formula, per-mile-only Zone X, MAX(origin, dest) VSC zone, and the NYC ZIP flat-rate override). |
| International quoting (stub) | 🚧 Read-only stub | `app/services/international_quote.py` computes the FSI `International Quotes!R21` math (per-lane min/per-lb + door-to-door km surcharge) against 1,099 pre-negotiated SC lanes seeded into `sc_international_lanes` by migration `d8a4f9c1b2e6`. No UI, no persistence, no VSC/accessorials/fuel — wire it into a route or the SC orchestrator when needed. |
| Booking email workflow (`Email to Request Booking`) | 🔒 Staff-only | Restricted to approved employees or super admins (or users with `can_send_mail` enabled). Customers see the button disabled. The composer's **Email to Book** button dispatches via Postmark (`POST /quotes/<id>/email/send`) with the requesting user as `Cc`; the **Open in mail client (fallback)** button keeps the legacy `mailto:` path for offline use. Every Postmark attempt persists an audit row in `booking_email_receipts` (`kind="single_quote"`). |
| SC multi-leg booking email (`Email Ops for Booking`) | ✅ Stable | Two-step workflow. Step 1 is an **intake form** (`/sc/quote/<id>/email-ops/intake`) that captures order-level shipper + consignee blocks plus pickup / delivery dates and persists them to `SCQuoteSession.booking_intake_json`. Step 2 is the **composer preview** (`/sc/quote/<id>/email-ops`) with four actions: **Email to Book** (`POST /sc/quote/<id>/email-ops/send`) sends via Postmark with ops on `To` and the requester on `Cc`; **Send to Myself** (`POST /sc/quote/<id>/email-ops/send-to-self`) dispatches the same rendered email to the logged-in user only (ops is NOT notified — useful as a preview / review step); **Copy body** clones the plain-text body to the clipboard; **Open in mail client (fallback)** keeps the `mailto:` link. The Postmark paths render the same plain-text body the `mailto:` link uses and additionally attach a multipart HTML alternative. The intake block (shipper / consignee / dates) appears above the per-leg shipment summary in all bodies when populated. **No $15 admin/booking fee** applied — multi-leg jobs bill off the raw cheapest-of total. Audit row per attempt in `booking_email_receipts` (`kind="sc_multi"` for ops sends, `kind="sc_multi_self"` for self-copies). |
| SC multi-leg lookup (`/sc/quote/lookup`) | ✅ Stable | Any SC user can resolve a multi-leg reference (`SCMQ0042` or a customer string) to the full persisted summary so they can re-send the ops booking email or pull a quote for a customer service call. |
| Volume-pricing email workflow | 🔒 Staff-only | Surfaces when a quote exceeds thresholds; limited to users with mail privileges. |
| Quote summary emailer | 🔒 Staff-only | Enabled for Freight Services staff only. Requires SMTP credentials and mail privileges. |
| Redis caching | ⚙️ Optional | Disabled by default. Enable with `COMPOSE_PROFILES=cache` and Redis configuration. |
| Variable Fuel Surcharge (VSC) | ✅ Stable (Hotshot + Air) | EIA-backed dynamic VSC is wired for both Hotshot and Air quotes. Both paths derive `fsc_pct` from the **destination** ZIP's VSC zone via `app.services.fuel_surcharge.get_vsc_pct_for_zone()`, matching the FSI Shipping Quote Tool 2026 VSC-Locked workbook (`Domestic Air Quotes!U5` = `VLOOKUP(dest_zip, 'VSC Zones', 4)`). Requires `setup_vsc_config.py` (run once) and weekly `sync_eia_rates.py`. Admin views at `/admin/settings/vsc-zones` and `/admin/settings/vsc-matrix`. |
| Microsoft Excel (Power Query) integration | ✅ Stable | POST quotes from Excel via `Web.Contents` with `ManualStatusHandling`. Set data source privacy to **Organizational** or **Public** — the default "Private" level blocks requests to external hosts. Use `&` for string concatenation and `Text.Proper` to normalize `quote_type`. See [FSI Quote Tool API Quick-Start](docs/fsi_quote_tool_api_quick_start.md) for a complete M-code example. |
| Google Sheets (Apps Script) integration | ✅ Stable | POST quotes via `UrlFetchApp.fetch`. Store the API key in script properties, not worksheet cells. |

Operator note: Air quotes enforce per-piece limits using billable weight (the greater of actual or dimensional weight).

## Documentation hub

- [Architecture](docs/architecture.md) – Component breakdown and reimplementation
  notes for porting the app to another stack.
- [Deployment](docs/deployment.md) – Production roll-out, TLS, and maintenance
  checklists.
- [FSI Quote Tool API Quick-Start](docs/fsi_quote_tool_api_quick_start.md) – External-integration quick start for authentication, endpoints, and examples.
- In-app Help Center (`/help`) – Task-oriented user guides rendered from
  `templates/help/`.
- Local setup references in this README:
  - [Quick Start (local development)](#quick-start-local-development)
  - [Database migrations](#database-migrations)
  - [Docker (production-style container)](#docker-production-style-container)
  - [Local development (Docker)](#local-development-docker)

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
   - If you use Docker Compose for local development, follow the
     [Local development (Docker)](#local-development-docker) section and set
     `POSTGRES_HOST` overrides when running helper scripts outside Docker.
3. Install packages: `pip install -r requirements-dev.txt`.
4. Create tables: `alembic upgrade head`.
5. Import ZIP and air rate data before starting the app:
   `python scripts/import_air_rates.py path/to/rates_dir`.
6. Seed the VSC configuration: `python scripts/setup_vsc_config.py`.
   This writes the `vsc_matrix`, `vsc_zones`, and `vsc_last_update` rows to
   the `AppSetting` table. Only needs to run once per environment; without it
   all quotes use 0% VSC.
7. Complete first-time setup at `/setup` to initialize the schema and create
   the first super admin account.
8. Run the app locally: `python flask_app.py`.
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
9. On first run, visit `/setup` to confirm environment variables, optionally
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
  --add-data "rates/Hotshot_Rates.csv;rates" `
  --add-data "rates/beyond_price.csv;rates" `
  --add-data "rates/accessorial_cost.csv;rates" `
  --add-data "rates/Zipcode_Zones.csv;rates" `
  --add-data "rates/cost_zone_table.csv;rates" `
  --add-data "rates/air_cost_zone.csv;rates" `
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

On first launch the executable seeds the database by invoking the setup
bootstrap process. The bundled rate data includes
`Hotshot_Rates.csv`, `beyond_price.csv`, `accessorial_cost.csv`,
`Zipcode_Zones.csv`, `cost_zone_table.csv`, and `air_cost_zone.csv`. Replace the
CSV files in the `rates` directory next to the executable (or its
`resources\rates` extraction folder when frozen) to load custom pricing the next
time you run the launcher.

`rates/air_cost_zone.csv` and the seeded `air_cost_zones` table are kept in
lock-step with the FSI Shipping Quote Tool 2026 VSC-Locked workbook
(`Domestic Air Quotes!C4:E11`) — that workbook is the company's authoritative
rate card. To realign an already-migrated production database with that card
after the CSV is updated, run `flask db upgrade head`; migration
`f3a8c2b9d1e4_align_air_cost_zones_with_fsi_vsc_locked.py` rewrites the eight
zones in the `default` rate set. Per-customer rate sets (anything other than
`'default'`) are NOT touched and must be reviewed manually.

Security guidance:

- Treat the generated `.env` as sensitive because it stores administrator
  credentials and API keys. Restrict NTFS permissions to the operations team and
  keep a copy in an enterprise secret vault rather than on shared drives.
- Rotate the Flask `SECRET_KEY` if the `.env` might have leaked. Run
  `windows_setup.exe --reconfigure` and press Enter when prompted for the key to
  create a new one.
- Update credentials recorded in `.env` (for example, `ADMIN_PASSWORD`) through
  your password manager, then rerun the launcher to synchronize the file.
- Review this README's setup sections and `docs/deployment.md` for additional
  background on seeding, migrations, and operational checks.

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
throttle abusive traffic. By default each client IP may perform up to 600
requests per day, 100 per hour, and 30 per minute, while the `/login` and
`/reset` endpoints are restricted to five POST attempts per minute for a given
IP and email combination. Override the defaults with environment variables as
needed:

| Variable | Purpose | Default |
| --- | --- | --- |
| `RATELIMIT_DEFAULT` | Global limits applied to all routes | `600 per day;100 per hour;30 per minute` |
| `RATELIMIT_STORAGE_URI` | Backend storage for counters | `memory://` |
| `AUTH_LOGIN_RATE_LIMIT` | Per-user/IP throttle for `/login` | `5 per minute` |
| `AUTH_RESET_RATE_LIMIT` | Per-user/IP throttle for `/reset` | `5 per minute` |
| `AUTH_RESET_TOKEN_RATE_LIMIT` | Frequency cap for issuing password reset tokens | `1 per 15 minutes` |

Set `RATELIMIT_HEADERS_ENABLED=true` to expose standard rate-limit headers if
your proxy or monitoring stack expects them.

### EIA fuel price sync

`scripts/sync_eia_rates.py` fetches current diesel prices from the U.S. Energy
Information Administration (EIA) API and upserts one `FuelSurcharge` row per
region. EIA publishes updated prices every Monday; schedule this script to run
weekly so quotes always reflect current fuel costs:

```bash
python scripts/sync_eia_rates.py
```

| Variable | Purpose | Default |
| --- | --- | --- |
| `EIA_API_KEY` | EIA API key for higher request rate limits. Omit for unauthenticated public access. | _(empty)_ |
| `EIA_SERIES_MAP_JSON` | JSON object overriding the built-in region→series mapping. Keys must match `FuelSurcharge.padd_region` values. | Built-in 11-region map |
| `EIA_TIMEOUT_SECONDS` | HTTP request timeout in seconds per EIA API call. | `15` |
| `EIA_COMMIT_STRATEGY` | `all_or_nothing` commits all regions in one transaction; `per_region` commits each independently so partial updates succeed. | `all_or_nothing` |

Every successful region sync explicitly bumps `FuelSurcharge.last_updated` to
the current UTC time, even when EIA returns an unchanged rate value. The three
admin pages that surface VSC freshness (`/admin/ria-rates`,
`/admin/settings/vsc-zones`, `/admin/settings/vsc-matrix`) all read
`MAX(fuel_surcharges.last_updated)` directly from the database. This is the
authoritative indicator of "the rates currently being served by the app" — and
because it queries the underlying rows rather than a separate sentinel, there
is exactly one timestamp to interpret. The `/admin/ria-rates` snapshot table
also surfaces each region's diesel price and per-region `last_updated`, so
admins can spot regions that may have failed to refresh in the most recent
sync. The legacy `vsc_last_update` `AppSetting` sentinel is no longer written
or read by the sync/snapshot flow.

### Rate CSV formats

The `Zipcode_Zones.csv` file must include a header row with these columns in
order:

1. `Zipcode`
2. `Dest Zone`
3. `BEYOND`

Headers must match exactly; missing or transposed columns will cause the import
script to raise an error.

For accurate VSC behavior, include `vsc zones.csv` in the same import directory.
The file must include `Zipcode` and `Dest Zone` columns. Import logic strips
non-digits from ZIP input, uses the first five digits, and upserts existing ZIP
rows so repeated runs stay idempotent.

### Local development (Docker)
For local Docker and Docker Compose workflows, see
[Quick Start (local development)](#quick-start-local-development) plus the
container workflow in [Docker (production-style container)](#docker-production-style-container).

### Bulk seeding user accounts

Use `scripts/seed_users.py` when you need to load several accounts at once.
The `scripts/` directory contains `users_seed_template.csv` with two example
rows. Copy the file, replace the placeholder values, and run the script:

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
# Optional: enable Google reCAPTCHA for /auth/register
RECAPTCHA_SITE_KEY=your_public_site_key
RECAPTCHA_SECRET_KEY=your_private_secret_key
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

## Post-login landing pages

When a user successfully authenticates (form login, OIDC SSO, or visiting the
`/` index while signed in), the server redirects them to a landing endpoint
based on their `rate_set`. The mapping lives in
`app/services/rate_sets.py` under `RATE_SET_LANDING_ENDPOINTS`:

| `rate_set` value | Landing endpoint | URL |
| --- | --- | --- |
| `scicr` | `science_care.sc_quote_form` | `/sc/quote` |
| `science_care` | `science_care.sc_quote_form` | `/sc/quote` |
| _(everything else)_ | `quotes.new_quote` | `/quotes/new` |

To give another rate set its own landing page, add an entry to
`RATE_SET_LANDING_ENDPOINTS`. The lookup runs the user's stored `rate_set`
through `normalize_rate_set` first, so the keys must be lowercase and
trimmed.

The `sc_user_required` and `sc_admin_required` policies in `app/policies.py`
treat both `scicr` and `science_care` as Science Care tenants, so users
landing on `/sc/quote` are not 403'd by the access gate.

`sc_admin_required` (the gate on `/sc/reference`) grants access to:

* FSI super-admins (`is_admin`).
* Freight Services employees identified by an `@freightservices.net` email
  address — internal staff get reference-table access automatically.
* Any user (including external customers) whose `User.is_sc_admin` flag is
  enabled via the admin user form's **Allow edit of Science care reference
  tables** checkbox. The flag is the explicit opt-in for non-FSI accounts
  that need to maintain Science Care reference data and is not bound to any
  particular rate set.

## Science Care multi-lab quote

The Science Care blueprint at `/sc` adds a 7-leg quote form that mirrors the
client's offline workbook. Each leg picks the cheapest of Air, Hotshot, or the
pre-negotiated Established Lane rate, and weight is rolled up from per-tissue
quantities plus consumables and box tare.

Each leg's **Destination ZIP** is paired with a read-only **City, State**
readout that resolves client-side via the Google Maps Geocoder as soon as the
ZIP reaches five digits — same `GOOGLE_MAPS_API_KEY` already wired into the
single-leg quote and email request flows. When the key is absent the readout
stays on its placeholder and no Maps request fires. The **Mode** dropdown
ships defaulted to `— select —` so the operator has to explicitly pick
**Frozen** or **Ready to Use**; a blank Mode short-circuits the temp_mode
consumable auto-default but otherwise quotes the leg normally.

The dest-ZIP field also fires a server-side HTMX call to
`/sc/quote/dest-zip-notes` that surfaces the matching `ZipZone.notes` row as
a yellow shipment-notes banner above the accessorials checklist — mirroring
the workbook's airport / cargo-warning row (e.g. *"Destination Airport Cargo
Warnings: Airtray Restrictions on Weekends"*). The lookup is scoped to the
science-care rate-set and falls back to the default rate-set's note when no
SC-specific row exists. The accessorial checklist itself carries two static
operator hints: a yellow *"These ancillary fees apply for both RETURNS &
PICK-UPS"* banner above the checkboxes, and each checkbox renders its
priced amount in parentheses (e.g. `Liftgate Required ($85.00)` or
`Driver Assistance (5%)`) sourced from `Accessorial.amount` via the
`SCAccessorialMap.accessorial_name` join. A blue info banner between the
tissue rows and the Boxes section repeats the workbook's box-sizing
guidance (*"Always quote the larger size if it shows a specimen can ship in
two different sizes"* and *"If shipping multiple tissue types together and
it's not intuitive, ask the lab how they would package"*).
Each leg also exposes a **This is a return** checkbox. When checked, the
orchestrator swaps the leg's origin and destination so the quote (and the
Established Lane lookup) prices from the typed customer ZIP **back to the
lab** instead of from the lab outbound. All other inputs — tissue rows,
boxes, consumables, accessorials, and the cheapest-of-three rollup — are
unchanged.

### Edit-mode prefill

The new **Edit a Quote** page (`/quotes/edit`) lets users look up a prior
quote by **Quote ID** (`Q-XXXXXXXX`) or **Client Reference** and open the
matching form prefilled, ready to tweak and resubmit as a brand-new
quote — the original row is never modified. Quote IDs land in
`/quotes/new` with origin / destination / weight / dimensions /
accessorials prefilled. Multi-leg references (`SCMQNNNN`) land in
`/sc/quote` with every per-leg input restored from
`SCQuoteSession.payload_json`, including tissue rows, box-count
overrides, consumable Qty overrides, the **Mode**, **Shipment type**,
**This is a return** checkbox, and accessorials. The `multi_reference`
and `client_reference` fields are intentionally left blank so the new
submission either auto-assigns a fresh `SCMQNNNN` or the user picks a
new value to avoid colliding with the original via the per-user UNIQUE
constraint. The same "Edit as new quote" entry point is surfaced as a
button on the existing single-quote (`/quotes/lookup`) and SC multi-leg
(`/sc/quote/lookup`) result pages.

### Per-tissue box capacities

Each tissue ships in a specific box size; some tissues fit multiple sizes with
different per-box quantities. The reference data captures one capacity per
(tissue, box) pair, matching the client's spreadsheet template:

| Tissue Code | Unit Weight (lb) | Medium | Large | X-Large | Small Airtray | Airtray |
| --- | --- | --- | --- | --- | --- | --- |
| `ARM01` (Arm Whole) | 12 | 0 | 7 | 10 | 0 | 0 |
| `CADV02` (Embalmed Cadaver) | 300 | 0 | 0 | 0 | 0 | 1 |

A value of `0` means the box size cannot ship that tissue. The allocator picks
the box that minimises the box count for the requested qty, with ties broken by
smaller interior volume. The user can override per-row via the Box dropdown on
each tissue line.

The data lives in two tables:

- `sc_tissue_codes` – one row per tissue (code, description, avg weight, notes).
- `sc_tissue_box_capacity` – one row per (tissue, box) with `pieces_per_box`.

Both round-trip via `/sc/reference/sc_tissue_codes/upload` and `download` using
a CSV that matches the client template column-for-column:

```
Tissue Code, Description, Unit Weight (lb), Medium, Large, X-Large, Small Airtray, Airtray, Notes
```

Box dimensions and tare weights live in `sc_box_types`. The migration seeds a
placeholder `SMALL_AIRTRAY` row with zero dimensions – an SC admin must populate
real dimensions before the allocator will pick that box.

### Live weight breakdown

The quote form shows the three weight components in two places:

**Per-row (tissue table)** — each tissue line shows **Avg lbs** (per piece) plus
**Total lbs** (`qty × avg`) and **Total kg** (`Total lbs × 0.4536`, rounded to a
whole kg). The Total cells update client-side as the user types qty so the
numbers stay responsive even between server round-trips.

**Per-leg (Shipment weight card)** — below the Boxes section a card shows live
subtotals for the leg:

- **Tissue** – `Σ qty × unit_weight` across the leg's tissue rows.
- **Consumables** – temperature-mode defaults with per-row override. A blank
  Qty falls back to the auto default for the matching row only:
  `temp_mode=frozen` adds **1 domestic dry ice per box**; `temp_mode=rtu`
  adds **1 domestic gel pack per box**. Non-matching consumable rows stay
  at 0 unless the user types a Qty. Any typed value wins (including `0`
  to suppress the default for that row), mirroring the
  "prefill blank only" semantic of the per-leg box-count overrides.
- **Box tare** – `Σ tare_weight × count` for every box on the leg.
- **TOTAL** – the three summed; matches the client workbook's
  "TOTAL SHIPMENT WEIGHT" cell.

The card updates whenever any of the leg's inputs change (tissue code, qty,
box override, consumable Qty) — all of them route through the same
`/sc/quote/leg/<n>/box-counts` HTMX endpoint which emits the subtotals card
as an OOB swap. The Consumables section now renders below Boxes since it's
the optional, last-step add-on to the leg's billable weight.

After running the multi-leg quote, the **results card** repeats the same three
columns per leg plus a Grand-total row, so the breakdown is visible during
form entry AND after pricing.

### Multi-leg reference, booking email, and lookup

Every SC multi-leg submission stores a unified **multi-leg reference** on its
`SCQuoteSession.multi_reference` column so all subsequent actions — the
aggregated booking email, the lookup page, even the per-leg `Quote` rows — can
be tied together by one identifier.

- **Customer-supplied**: a `multi_reference` input on `/sc/quote` accepts any
  upper-cased alphanumeric value (plus `-`, `_`, `/`, space) up to 57 chars
  (the cap leaves room for the worst-case `-L7-HOT` suffix appended to each
  leg's `Quote.client_reference` column) and is rejected if already in use.
- **Auto-assigned**: blank submissions get the next `SCMQNNNN` (Science Care
  Multi-Quote ####), starting at `SCMQ0001`. The numeric tail grows past 9999
  without re-padding (`SCMQ10000`).
- **Per-leg stamp**: every underlying `Quote` row created by `create_quote()`
  carries `client_reference=<multi_reference>-L<leg>-<AIR|HOT>` so a customer
  can still look up a single leg by its suffixed string.

**Email Ops for Booking** is a two-step workflow. The "Email Ops for Booking"
button on the SC results card routes through an **intake form** first
(`GET /sc/quote/<session_id>/email-ops/intake`) that captures order-level
shipper and consignee blocks (name, contact, address, phone, reference,
notes) plus pickup / delivery dates. The form is pre-filled from
`SCQuoteSession.booking_intake_json` on every visit so re-loading the page
recalls what the user typed; the parser accepts any subset, so a user can
skip the form via the "Skip and use existing details" link if they want to
send without an intake block. Submitting the intake form
(`POST /sc/quote/<session_id>/email-ops/intake`) persists the JSON and
redirects to the composer preview.

The **composer preview** (`GET /sc/quote/<session_id>/email-ops`) renders
both a plain-text body and an HTML preview of the aggregated booking
message. The captured intake (when present) shows above the per-leg shipment
summary in both bodies and as a read-only "Booking details" card on the
page with an "Edit booking details" link back to the intake form. The view
intentionally adds **no** admin / booking fee — that $15 fee is specific
to the single-quote `/quotes/<id>/email` workflow. Multi-leg jobs bill off
the raw cheapest-of total.

The composer exposes four actions:

* **Email to Book** — `POST /sc/quote/<session_id>/email-ops/send`. Dispatches
  the message via the existing Postmark SMTP path (`app/services/mail.py`
  `send_email()`) as a `multipart/alternative` email: the plain-text body
  shown in the preview plus a richly formatted HTML rendering. Recipient is
  configurable via `BOOKING_EMAIL_OPS_TO` (default
  `operations@freightservices.net`); the requesting user is added as `Cc`
  so they keep a copy in their inbox. Every attempt — successful or not —
  persists a row in the `booking_email_receipts` audit table (see
  `kind="sc_multi"`) with the Postmark message id, recipient list, and any
  error text.
* **Send to Myself** — `POST /sc/quote/<session_id>/email-ops/send-to-self`.
  Same body, same audit-row plumbing, same intake gating, but the recipient
  is the logged-in user's email address with **no** ops `To` or `Cc`. Lets
  the user review the rendered email or forward it elsewhere without
  copying ops on a draft. Audit row tagged `kind="sc_multi_self"` so the
  booking pipeline can distinguish self-sends from the real ops send.
* **Copy body** — copies the plain-text body to the clipboard for ops who
  prefer to paste into their own template.
* **Open in mail client (fallback)** — the legacy `mailto:` link, kept
  intentionally so ops can still send from their own desktop client when
  Postmark is unreachable or rate-limited. Uses the same plain-text body the
  Postmark send uses, so all paths render identical content.

The email body groups every leg's items by shipment segment and prints a
**weight summary** so ops can sanity-check the billable weight without
re-keying the form. Each leg lists its tissue rows (with unit + line weight),
boxes used (count × tare = line weight), and consumables used (qty × per-box
weight), each followed by a per-segment subtotal. A final
`Shipment weight summary` line per leg sums the three subtotals; this matches
the `LegResult.tissue_weight_lb + box_tare_weight_lb + consumable_weight_lb`
breakdown documented as **EQ-013** in `docs/equations.md`.

**Lookup** (`/sc/quote/lookup`) is SC-scoped across users: any SC user can
resolve any `SCMQNNNN` (or customer-supplied reference) to the persisted
multi-leg summary. This is intentional — customer service uses it to help
customers find prior jobs by reference.

### Reference table admin

SC admins can manage each reference table individually from
`/sc/reference`. Every table card exposes three actions:

* **View / Edit Rows** (`/sc/reference/<table>`) — paginated list with
  per-row **Edit** and **Delete** buttons plus an **Add Row** action.
  Mirrors the inline maintenance UX at `/admin/accessorials` (the form
  is generated from each table's `TableSpec` columns so the parsers and
  validators match the CSV import path). The tissue-codes form is
  augmented with one **Pieces / box** input per existing SC box type;
  the parent row's legacy `default_box_type_code` + `pieces_per_box`
  hint is recomputed automatically from the box with the largest
  capacity, matching the CSV importer's behaviour.
* **Download CSV** (`/sc/reference/<table>/download`) — snapshot of the
  SC tenant's rows.
* **Upload CSV** (`/sc/reference/<table>/upload`) — bulk replace or
  append, dedupe by primary key.

All paths are tenant-scoped (rows from other rate-sets are never
included, overwritten, edited, or deleted) and gated by
`sc_admin_required`. Reference-table access is granted to FSI
super-admins, `@freightservices.net` employees, and any user the admin
checkbox **Allow edit of Science care reference tables** is set for —
see the rate-set routing section above for the full policy.

## Advanced

- Run only the JSON API: `python standalone_flask_app.py`.
- Import rate data any time with the same script as above.
- Admin pages let you manage rate tables and review the active Variable Fuel Surcharge (VSC) configuration at `/admin/settings/vsc-zones` and `/admin/settings/vsc-matrix`. To refresh diesel prices, run `python scripts/sync_eia_rates.py` (or schedule it weekly). To reset VSC defaults, run `python scripts/setup_vsc_config.py`.

### JSON API authentication

The JSON API requires an API token in the `Authorization` header for every
request. Configure the token with `API_AUTH_TOKEN` and provide it as a bearer
token. API error responses include both `error` and `remediation` fields so
clients can display actionable next steps:

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
this file automatically), then run `alembic upgrade head` and finish first-run
configuration at `/setup`.

If quotes warn that "Air rate table(s) missing or empty", ensure the CSV
directory is present or specify its path via `RATE_DATA_DIR` before initializing
the database.

## Architecture

See [docs/architecture.md](docs/architecture.md) for an overview of the application's components and guidance on rebuilding the app in another stack.
