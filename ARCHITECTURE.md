# Application Architecture and Reimplementation Guide

This document describes the structure and behavior of the Quote Tool so a developer can rebuild it in a different stack while preserving all functionality. For a curated map of related guides and inline code commentary, see [docs/README.md](docs/README.md).

## Overview

Quote Tool is a web application for generating freight quotes for **Hotshot** (expedited truck) and **Air** shipments. Users can register, log in, calculate quotes, and email quote details. Administrators manage users and rate tables.

The application exposes both HTML pages and JSON APIs and persists data in a SQL database via SQLAlchemy.

## Technology Stack

- **Language:** Python 3.8+
- **Web Framework:** Flask with Blueprints
- **Database:** postgress cloud sql
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
- `theme.py` and `admin_view.py` – presentation helpers and admin pages.

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

**`air_quote(w, a, wb, r_lb, mc, oc, dc)`**

- Variables: `w` = billable weight (lb), `a` = accessorial total, `wb` = weight break (lb), `r_lb` = rate per pound, `mc` = minimum charge, `oc` = origin beyond charge, `dc` = destination beyond charge.
- Function:

  - Base charge: `mc` if `w ≤ wb` else `(w - wb) × r_lb + mc`
  - Quote total: `base + a + oc + dc`

**`guarantee_cost(base, g)`**

- Variables: `base` = base charge plus any beyond charges, excluding other accessorials, `g` = guarantee percentage.
- Function: `base × (g / 100)`

### Services Layer (`services` package)
- `auth_utils.py` – password/email validation and password reset token handling.
- `hotshot_rates.py` – retrieval and management of hotshot rate records.
- `quote.py` – orchestrates quote creation, accessorial cost calculations, and database persistence.
- `mail.py` – validates sender domains, enforces mail privileges, applies rate limits, and logs outbound email usage.
- `settings.py` – exposes runtime overrides so super admins can adjust mail and limiter configuration from the dashboard.

### Feature status at release

| Feature | Status | Notes |
| --- | --- | --- |
| Hotshot and Air quoting | ✅ Stable | Core workflow used in production. |
| Booking email workflow | 🔒 Staff-only | Restricted to approved Freight Services staff via `services.mail.user_has_mail_privileges`. |
| Volume-pricing email workflow | 🔒 Staff-only | Enabled only when a quote exceeds thresholds; shares the same privilege checks. |
| Admin quote history | ✅ Stable | Available at `/admin/quotes` with CSV export at `/admin/quotes.csv`. |
| Redis caching profile | ⚙️ Optional | Disabled unless Redis is provisioned and the `cache` profile is active. |

## External Configuration

The application relies on several environment variables (see `.env.example`):
- `DATABASE_URL` or google clouod sql
- `SECRET_KEY` for session signing
- `GOOGLE_MAPS_API_KEY` for distance lookups
- Admin bootstrap credentials (`ADMIN_EMAIL`, `ADMIN_PASSWORD`)

## Reimplementation Notes

To rebuild the app in another language or framework:
1. **Data Model** – replicate the tables defined in `app/models.py` with equivalent relationships and constraints.
2. **Auth Flow** – implement registration, login, logout, and password reset using secure password hashing and token-based resets.
3. **Quote Engine** – port the algorithms from `quote/logic_hotshot.py` and `quote/logic_air.py`, including dimensional weight logic and accessorial handling found in `services/quote.py`.
4. **Distance Lookups** – provide a service wrapper around the Google Maps Directions API similar to `quote/distance.py`.
5. **Admin Functions** – include interfaces for managing users and rate tables as in `app/admin.py` and `quote/admin_view.py`.
6. **Email** – expose a way to email quote summaries using configurable SMTP settings (`app/__init__.py::send_email`).
7. **APIs and Templates** – replicate the routes in `flask_app.py` and the blueprints, adapting templates or JSON endpoints as desired.

With these components in place, any stack can reproduce the behavior of the Quote Tool while tailoring presentation or infrastructure to new requirements.

## Testing

The original project uses `pytest`. After reimplementation, ensure equivalent unit tests cover authentication, rate imports, quoting logic, and API routes.

