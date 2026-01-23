# Quote Tool

Quote Tool is a web app for quick freight quotes. It handles Hotshot and Air jobs for Freight Services staff and trusted partners.
Enter ZIP codes, weight, and extras to see a price,
QUOTE TOOL FOR fsi DESIGNED TO WORK ON GOOGLE CLOUD RUN

## Features

- Sign up, log in, and reset passwords
- Global request throttling protects login and password reset endpoints
- Freight Services sign-ups using `@freightservices.net` emails are created as
  pending employees so administrators can approve their access
- Admin area to approve users, edit rates, and review the quote history
- Price engine uses Google Maps and rate tables
- Quotes saved in a database
- Warns when shipment weight or cost exceeds tool limits

- ## Feature status

| Feature | Status | Notes |
| --- | --- | --- |
| Hotshot and Air quoting | ✅ Stable | Accepts form and JSON submissions and persists quotes. |
| Booking email workflow (`Email to Request Booking`) | 🔒 Staff-only | Restricted to approved employees or super admins whose email matches `MAIL_PRIVILEGED_DOMAIN`. Customers see the button disabled. |
| Volume-pricing email workflow | 🔒 Staff-only | Surfaces when a quote exceeds thresholds; limited to users with mail privileges. |
| Quote summary emailer | 🔒 Staff-only | Enabled for Freight Services staff only. Requires SMTP credentials and mail privileges. |

## Documentation hub

- [Documentation Hub](docs/README.md) – Cross-reference of every guide, inline
  comment, and help topic.
- [Architecture](ARCHITECTURE.md) – Component breakdown and reimplementation
  notes for porting the app to another stack.
- [Deployment](DEPLOYMENT.md) – Production roll-out, TLS, and maintenance
  checklists.
- In-app Help Center (`/help`) – Task-oriented user guides rendered from
  `templates/help/`.

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
install the dependencies and configure your environment variables. From the
project root, execute:

```bash
pytest
```

This command discovers and runs all tests in the `tests/` directory. Use it to
verify changes before deploying or opening a pull request.

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

### Docker

To start the full stack with Docker Compose, run the following command from the
repository root:

```bash
docker compose up -d postgres
docker compose up -d quote_tool swag
```

Compose builds the images (if needed) and launches the services defined in
`docker-compose.yml`. Bring up the `postgres` container first so the health
check passes before the application starts. The stack automatically loads
environment variables from a `.env` file that sits alongside the Compose file.
Create `./data/postgres` ahead of time and ensure it is owned by the UID/GID
specified by `PUID`/`PGID`; for example:

```bash
mkdir -p data/postgres
sudo chown 1000:1000 data/postgres  # Replace 1000 with your deployment user IDs.
```

After the services start, apply migrations with Alembic:

```bash
docker compose run --rm quote_tool alembic upgrade head
```

#### Enable Redis caching

The Compose stack ships with an optional Redis service that accelerates page
rendering and centralises rate-limit counters. To enable it without editing
source files:

1. Add `COMPOSE_PROFILES=cache` to your `.env` file. The helper also respects
   `COMPOSE_PROFILES` exported in your shell before running `docker compose`.
2. Optionally override the cache settings:
   - Leave `CACHE_TYPE`, `CACHE_REDIS_URL`, and `RATELIMIT_STORAGE_URI` unset to
     accept the defaults generated by `config.Config` when the `cache` profile
     is active (`redis://redis:6379/0` for the application cache and
     `redis://redis:6379/1` for Flask-Limiter).
   - Provide custom values if you prefer an external Redis instance.
3. Start the service with `docker compose --profile cache up -d redis` or bring
   up the full stack with `docker compose --profile cache up -d`.
4. Verify connectivity by running `docker compose exec redis redis-cli PING`. A
   successful setup prints `PONG`.

Create `./data/redis` before the first run (step 3 in the quick start) and
match its ownership to `PUID`/`PGID` so LinuxServer's init scripts can persist
data across container restarts.

```bash
# Build the image
docker build -t quote_tool .

# Start the container and expose port 5000 (debug disabled)
docker run -d --name quote_tool -p 5000:5000 quote_tool

# Start a separate container with the debugger enabled for local work
docker run -d --name quote_tool_dev -e FLASK_DEBUG=1 -p 5000:5000 quote_tool

# Seed an admin user inside the container
docker exec -e ADMIN_EMAIL=admin@example.com -e ADMIN_PASSWORD=change_me \
  quote_tool python init_db.py
```

The final command seeds an admin user; replace the example email and password
with your own credentials. It also loads all rate tables from the bundled
CSV files in the repository root. Set `RATE_DATA_DIR` to point to a custom
directory if needed. If `ADMIN_EMAIL` and `ADMIN_PASSWORD` are defined in a
`.env` file, `init_db.py` loads them automatically and the `-e` flags can be
omitted.

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
