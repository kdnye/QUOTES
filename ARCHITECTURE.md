# Application Architecture and Reimplementation Guide

This document describes the structure and behavior of the Quote Tool so a developer can rebuild it in a different stack while preserving all functionality. For a curated map of related guides and inline code commentary, see [docs/README.md](docs/README.md).

## Overview

Quote Tool is a web application for generating freight quotes for **Hotshot** (expedited truck) and **Air** shipments. Users can register, log in, calculate quotes, and email quote details. Administrators manage users and rate tables.

The application exposes both HTML pages and JSON APIs and persists data in a SQL database via SQLAlchemy.

## Technology Stack

- **Language:** Python 3.8+
- **Web Framework:** Flask with Blueprints
- **Database:** PostgreSQL (Cloud SQL)
- **Auth:** flask-login sessions and CSRF protection via Flask-WTF
- **Front End:** Jinja2 templates and Bootstrap-based theme
- **External Services:** Google Maps Directions API for mileage lookups

## High-Level Components

```
flask_app.py            - Development entry point
app/                    - Core application package
  __init__.py           - create_app, config, blueprints
  models.py             - SQLAlchemy models
  auth.py               - registration/login/password reset routes
  admin.py              - admin dashboard and rate management
  quotes/               - quote creation and email routes
  admin_view.py         - admin-only quote history and CSV export helpers
quote/                  - Pricing logic and helpers
services/               - Business logic wrappers for auth and quotes
```

### Application Factory (`app/__init__.py`)
- Initializes Flask, database, login manager, CSRF protection, and the Limiter integration.
- Registers blueprints: `auth`, `admin`, `admin_quotes`, `quotes`, and `help`.
- Utility helpers:
  - `build_map_html` embeds a Google Maps iframe to show directions.
  - `send_email` sends SMTP messages based on app config after enforcing `services.mail` rate limits and domain checks.
  - `_verify_app_setup` ensures essential tables and templates exist before serving traffic.

### Database Models (`app/models.py`)
Key tables:
- `User` – registered users with hashed passwords and admin flag.
- `Quote` – stored quotes including origin, destination, weight, pricing metadata, and generated UUID.
- `EmailQuoteRequest` – supplemental shipping details collected when emailing a quote.
- `Accessorial`, `HotshotRate`, `BeyondRate`, `AirCostZone`, `ZipZone`, `CostZone` – rate tables that drive pricing.
- `AppSetting` – key/value store for database-persisted runtime configuration. The VSC feature uses three keys: `vsc_matrix` (JSON tier table mapping diesel $/gal ranges to surcharge percentages), `vsc_zones` (JSON object mapping destination zone numbers 1–10 to PADD region labels), and `vsc_last_update` (ISO timestamp of the last VSC config seed).
- `FuelSurcharge` – one row per EIA PADD region storing the current diesel price (`current_rate`, $/gal) and `last_updated` timestamp. Populated by `scripts/sync_eia_rates.py`; queried by `app/services/fuel_surcharge.py` to resolve per-zone surcharge percentages at quote time.

### Authentication (`app/auth.py`)
- Routes for login, registration, logout, password reset request, and token-based reset.
- Uses helpers in `services.auth_utils` for validation and token management.

### Quote Workflow (`app/quotes/routes.py`)
- `/quotes/new` displays the form for creating quotes or accepts JSON payloads.
- Retrieves accessorial options from the database, calculates dimensional weight, and delegates pricing to the `quote` package.
- Saves the resulting `Quote` and returns HTML or JSON.
- `/quotes/<quote_id>/email` gathers booking information and prepares an email for staff with mail privileges.
- `/quotes/<quote_id>/email-volume` escalates overweight/overvalue shipments for manual review without applying the admin fee.

### Pricing Logic (`quote` package)
- `distance.py` – wraps Google Maps Directions API with retry logic.
- `logic_hotshot.py` – computes hotshot quotes based on distance, zone, and rate tables.
- `logic_air.py` – computes air quotes using zone lookups and beyond charges.
- `thresholds.py` – enforces quote safeguards via `check_thresholds` for warning-level limits and `check_air_piece_limit` for Air per-piece billable-weight validation.
- `theme.py` and `admin_view.py` – presentation helpers and admin pages.

#### Variable Fuel Surcharge pipeline

All quotes (Hotshot and Air) apply a dynamic VSC percentage sourced from weekly EIA diesel prices. The four-step pipeline is:

1. **Sync** – `scripts/sync_eia_rates.py` fetches the latest weekly diesel price for each PADD region from the EIA v2 API and upserts one `FuelSurcharge` row per region (11 regions including NATIONAL as fallback). Run weekly; EIA publishes on Mondays.
2. **Zone lookup** – `logic_hotshot.py` / `logic_air.py` call `get_vsc_pct_for_zone(dest_zone)` with the numeric destination zone from the `ZipZone` table.
3. **Region resolution** – `fuel_surcharge.py::resolve_padd_region` maps the zone number to a PADD region label using the `vsc_zones` AppSetting (falls back to `NATIONAL` if the zone is absent).
4. **Matrix lookup** – `fuel_surcharge.py::lookup_matrix_pct` scans the `vsc_matrix` AppSetting tiers to find the tier where `min ≤ diesel_price < max` and returns that tier's `pct` (decimal fraction). Returns `0.0` on any failure so quotes are never blocked.

Zone-to-PADD-region reference:

| Zone | States | PADD Label | EIA Region |
|------|--------|------------|------------|
| 1 | IL KY IN OH TN | PADD1 | East Coast |
| 2 | CT ME MA NH RI VT | PADD1A | New England |
| 3 | NY NJ DE PA MD VA WV DC | PADD1B | Central Atlantic |
| 4 | NC SC GA | PADD1C | Lower Atlantic |
| 5 | MI WI MN IA MO ND SD KS NE | PADD2 | Midwest |
| 6 | TX LA OK AR FL AL MS | PADD3 | Gulf Coast |
| 7 | ID CO MT WY UT NM | PADD4 | Rocky Mountain |
| 8 | NV AZ | PADD5 | West Coast |
| 9 | CA HI AK | CA | California |
| 10 | WA OR | PADD5XCA | West Coast excl. CA |

#### Quote Calculation Formulas

The pricing modules implement the following core functions:

**`dim_weight(L, W, H, P)`**

- Variables: `L` = length in inches, `W` = width in inches, `H` = height in inches, `P` = number of pieces.
- Function: `((L × W × H) / 166) × P`

**`billable_weight(actual, dimensional)`**

- Variables: `actual` = actual shipment weight in pounds, `dimensional` = dimensional weight in pounds.
- Function: `max(actual, dimensional)`

**`hotshot_quote(m, w, a, r_lb, f, mc, z)`**

- Variables: `m` = distance in miles, `w` = billable weight (lb), `a` = accessorial total, `r_lb` = rate per pound, `f` = fuel surcharge as a decimal, `mc` = minimum charge, `z` = zone code.
- Function:

  - If `z` = "X": `m × mc × (1 + f) + a`
  - Else: `max(mc, w × r_lb) × (1 + f) + a`

> **Note:** `f` is not a hardcoded constant. In the current implementation it is resolved at runtime by `get_vsc_pct_for_zone(dest_zone)` in `logic_hotshot.py`, which combines any base surcharge with a dynamic VSC percentage derived from the EIA regional diesel price for the destination zone. Reimplementations must replicate this dynamic lookup rather than hardcoding a fuel surcharge rate.

**`air_quote(w, a, wb, r_lb, mc, oc, dc)`**

- Variables: `w` = billable weight (lb), `a` = accessorial total, `wb` = weight break (lb), `r_lb` = rate per pound, `mc` = minimum charge, `oc` = origin beyond charge, `dc` = destination beyond charge.
- Function:

  - Base charge: `mc` if `w ≤ wb` else `(w - wb) × r_lb + mc`
  - Quote total: `base + a + oc + dc`

> **Note:** Air quotes also apply a dynamic VSC surcharge via `get_vsc_pct_for_zone(dest_zone)` in `logic_air.py`. The VSC amount is computed from the same EIA-backed pipeline described above and added to the quote total. Reimplementations must include this lookup.

**`guarantee_cost(base, g)`**

- Variables: `base` = base charge plus any beyond charges, excluding other accessorials, `g` = guarantee percentage.
- Function: `base × (g / 100)`

#### Validation & Limits

- Air quote warning is generated when billable weight exceeds `1200` lb.
- Generic quote warning is generated when billable weight exceeds `3000` lb or quote total exceeds `$6000`.
- Air quote validation fails when billable pounds per piece exceed `300`, where billable weight is calculated as `max(actual_weight, dim_weight)` and then divided by `pieces`.
- These rules are enforced in `app/quotes/routes.py` during quote submission: piece-limit failures can block quote creation, while threshold checks annotate the quote with warnings.

### Services Layer (`services` package)
- `auth_utils.py` – password/email validation and password reset token handling.
- `hotshot_rates.py` – retrieval and management of hotshot rate records.
- `quote.py` – orchestrates quote creation, accessorial cost calculations, and database persistence.
- `mail.py` – validates sender formatting, enforces mail privileges, applies rate limits, and logs outbound email usage.
- `settings.py` – exposes runtime overrides so super admins can adjust mail and limiter configuration from the dashboard.
- `fuel_surcharge.py` – Variable Fuel Surcharge computation. `get_vsc_pct_for_zone(dest_zone)` is the public entry point: reads `vsc_zones` and `vsc_matrix` from `AppSetting`, queries `FuelSurcharge` for the matching PADD region (fallback: NATIONAL), scans the matrix tiers, and returns a decimal surcharge percentage. Returns `0.0` on any configuration or database error so quotes are never blocked.

### Feature status at release

| Feature | Status | Notes |
| --- | --- | --- |
| Hotshot and Air quoting | ✅ Stable | Core workflow used in production. |
| Booking email workflow | 🔒 Staff-only | Restricted to approved employees or super admins (or users with `can_send_mail`) via `services.mail.user_has_mail_privileges`. |
| Volume-pricing email workflow | 🔒 Staff-only | Enabled only when a quote exceeds thresholds; shares the same privilege checks. |
| Admin quote history | ✅ Stable | Available at `/admin/quotes` with CSV export at `/admin/quotes.csv`. |
| Redis caching profile | ⚙️ Optional | Disabled unless Redis is provisioned and the `cache` profile is active. |
| Variable Fuel Surcharge (VSC) | ✅ Stable | Dynamic per-zone surcharge applied to all Hotshot and Air quotes. Requires `setup_vsc_config.py` (one-time seed) and weekly `sync_eia_rates.py` runs. |

## External Configuration

The application relies on several environment variables (see `.env.example`):
- `DATABASE_URL` or Google Cloud SQL
- `SECRET_KEY` for session signing
- `GOOGLE_MAPS_API_KEY` for distance lookups
- Admin bootstrap credentials (`ADMIN_EMAIL`, `ADMIN_PASSWORD`)
- `EIA_API_KEY` – authenticates calls to the EIA v2 API from `sync_eia_rates.py`; optional (omit for unauthenticated public access)
- `EIA_SERIES_MAP_JSON` – overrides the built-in region→EIA-series mapping; keys must match `FuelSurcharge.padd_region` values
- `EIA_TIMEOUT_SECONDS` – HTTP timeout per EIA request (default `15` seconds)
- `EIA_COMMIT_STRATEGY` – transaction strategy for `sync_eia_rates.py`: `all_or_nothing` (default) or `per_region`

## Reimplementation Notes

To rebuild the app in another language or framework:
1. **Data Model** – replicate the tables defined in `app/models.py` with equivalent relationships and constraints.
2. **Auth Flow** – implement registration, login, logout, and password reset using secure password hashing and token-based resets.
3. **Quote Engine** – port the algorithms from `quote/logic_hotshot.py` and `quote/logic_air.py`, including dimensional weight logic and accessorial handling found in `services/quote.py`.
4. **Distance Lookups** – provide a service wrapper around the Google Maps Directions API similar to `quote/distance.py`.
5. **Variable Fuel Surcharge** – replicate the three-layer VSC pipeline: a weekly data-fetch job equivalent to `sync_eia_rates.py` that writes current diesel $/gal to per-region rows; a configuration store equivalent to `AppSetting` holding the zone-to-region map (`vsc_zones`) and the tier matrix (`vsc_matrix`); and a lookup service equivalent to `fuel_surcharge.py` that resolves zone → region → diesel price → tier percentage at quote time, returning `0.0` gracefully on any missing data.
6. **Admin Functions** – include interfaces for managing users and rate tables as in `app/admin.py` and `quote/admin_view.py`. Also include read-only admin views for the VSC zone map (`/admin/settings/vsc-zones`) and tier matrix (`/admin/settings/vsc-matrix`), equivalent to `view_vsc_zones` and `view_vsc_matrix` in `app/admin.py`.
7. **Email** – expose a way to email quote summaries using configurable SMTP settings (`app/__init__.py::send_email`).
8. **APIs and Templates** – replicate the routes in `flask_app.py` and the blueprints, adapting templates or JSON endpoints as desired.

With these components in place, any stack can reproduce the behavior of the Quote Tool while tailoring presentation or infrastructure to new requirements.

## Testing

The original project uses `pytest`. After reimplementation, ensure equivalent unit tests cover authentication, rate imports, quoting logic, and API routes.
