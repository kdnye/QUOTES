# Claude Code Instructions — FSI Ecosystem

This file is read by Claude Code at the start of every session. All rules below
apply whenever Claude works in **any FSI repository**.

---

## 1. README Documentation Standard

**Every time you add, change, or remove a feature, update `README.md`.**

The README must always contain the following sections in order:

```
# <App Name>
One-sentence description.

## Quick Start
Step-by-step: clone → install deps → set env vars → run locally.
Every command must be copy-pasteable and tested.

## Environment Variables
Table: variable | required? | default | description.
Group by category (Database, Auth, Mail, OIDC, etc.).

## Architecture
Prose + diagram. Describe:
  - Request lifecycle (browser → nginx/Cloud Run → Flask → DB)
  - Blueprint/module breakdown
  - Auth flow (session, OIDC paths)
  - RBAC model (roles, hierarchy, policy matrix)
  - Background jobs if any

## Database
  - Schema overview (tables and relationships)
  - How to run migrations: `flask db upgrade`
  - How to create a new migration: `flask db migrate -m "description"`

## Running Tests
  `pytest` — show sample output
  Coverage thresholds and what to do if they fail.

## Deployment
  Cloud Run deploy command.
  Secrets wiring via Secret Manager.
  Health check endpoint.

## Equations & Business Logic
  Link to `docs/equations.md` (see Section 2 below).
```

### Documentation Rules

- Write for someone who has **never seen this codebase** and needs to get it
  running in under 30 minutes.
- Every env var that is required in production must be listed. No exceptions.
- When you add a new blueprint, service, or model, add a bullet to the
  Architecture section explaining its purpose.
- Keep the Quick Start commands working. If you change the startup command,
  update the README in the same commit.

---

## 2. Equations & Business Logic Documentation

**Every formula, threshold, or calculation used in the code must be documented
in `docs/equations.md`.** This file is the authoritative human-readable record
of all business logic — independent of the code, so it can be reviewed and
cross-checked without reading Python.

### Format for each equation entry

```markdown
## EQ-<NNN>: <Short Name>

**Purpose:** What this calculates and why.

**Formula:**
  result = <formula in plain math notation>

**Variables:**
| Variable | Type | Unit | Source | Description |
|----------|------|------|--------|-------------|
| ...      | ...  | ...  | ...    | ...         |

**Constraints:**
  - List any min/max bounds, edge cases, or special handling.

**Code location:** `app/path/to/file.py`, function `function_name()`, line ~NN

**Last verified:** YYYY-MM-DD
```

### Rules

- Assign a unique sequential ID (EQ-001, EQ-002, …) when you document a new
  equation. Never reuse an ID even if an equation is removed.
- If you change a formula in code, update the corresponding entry in
  `docs/equations.md` in the **same commit**.
- If you remove a formula, mark it `**[REMOVED YYYY-MM-DD]**` — do not delete
  the entry (audit trail).
- Examples must include at least one worked numeric example showing
  inputs → expected output so a non-developer can verify the formula manually.
- The `docs/equations.md` file must have a summary table at the top listing
  all equation IDs, names, and the file where they're implemented.

---

## 3. Architecture Documentation

Maintain `docs/architecture.md` with:

- **System diagram** (ASCII or Mermaid) showing all external services
  (Cloud Run, Cloud SQL, Redis, Secret Manager, SMTP, OIDC provider).
- **Data flow** for each major user journey (login, quote creation, password
  reset, admin actions).
- **Security boundaries** — which routes require authentication, which require
  specific roles, which are public.
- **Dependency map** — which services depend on which (e.g., rate limiting
  needs Redis in production).

Update `docs/architecture.md` whenever you add a new external service,
blueprint, or authentication path.

---

## 4. General FSI Ecosystem Standards

Apply these in every FSI repo:

### Security
- All passwords hashed with `werkzeug.security.generate_password_hash`.
- Password reset tokens: `secrets.token_urlsafe(32)`, SHA-256 hashed before
  storage, 24-hour expiry, `secrets.compare_digest` for comparison.
- CSRF protection on all state-changing routes (Flask-WTF).
- Rate limiting on all auth endpoints (login, register, reset).
- OIDC employee SSO restricted to `@freightservices.net` domain.
- Secrets in production via GCP Secret Manager — never in env files or code.
- `SESSION_COOKIE_SECURE = True` and `SESSION_COOKIE_HTTPONLY = True` in prod.

### Database
- All table name strings defined as constants before use in models.
- SQLAlchemy `pool_pre_ping=True` and `pool_recycle=1800` always.
- Database schema changes via Alembic/Flask-Migrate migrations only —
  never `db.create_all()` in production.
- `MIGRATE_ON_STARTUP=true` in Cloud Run to auto-apply migrations on deploy.

### Infrastructure
- Every app must expose `GET /healthz` returning `{"status": "ok"}`.
- Custom HTTP error handlers for 401, 403, 404, 429, 500 — HTML for browsers,
  JSON for `Accept: application/json` requests.
- Structured JSON logging (`CloudJsonFormatter`) for Cloud Logging.
- Non-root user in Dockerfile.
- Gunicorn via `scripts/start_gunicorn.sh`.

### Code Organization
- Business logic in `app/services/`, not in route handlers.
- Route handlers validate input, call services, return responses.
- All new services go in `app/services/`, not at the project root.
- New blueprints registered in `create_app()` in `app/__init__.py`.

### Testing
- Test file for every new service, model, or route file.
- Fixtures in `tests/conftest.py` — shared `app`, `client`, `create_user`,
  `logged_in_client`.
- CSRF and rate limiting disabled in test config.
- SQLite in-memory DB for tests.
- Run tests with: `pytest`

---

## 5. Commit & PR Standards

- Commit message format: `<type>: <short description>`
  Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
- If a commit changes a formula, it must also update `docs/equations.md`.
- If a commit adds a feature, it must also update `README.md`.
- PRs must include a test plan in the description.

---

## 6. Quotes App — Equations Reference

This app contains freight rate calculations. All formulas must be documented
in `docs/equations.md`. Key areas to document:

- Hotshot rate calculation (per_lb, per_mile, fuel surcharge)
- Air freight rate calculation (dimensional weight, per-lb zone rates)
- Dimensional weight divisor (166)
- Fuel surcharge percentage by PADD region
- Accessorial charge calculation (fixed vs. percentage)
- Weight break thresholds and minimum charge enforcement
- Zone determination logic from ZIP code pairs
- VSC (Variable Surcharge) zone percentage calculation
- Beyond charge flat fee application

When adding or modifying any of these calculations, update `docs/equations.md`
with the EQ-NNN entry format defined in Section 2 above.
