"""Setup blueprint for first-run configuration flows."""

from __future__ import annotations

import os
from typing import Any, Mapping

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app.database import ensure_database_schema
from app.models import User, db
from app.services.auth_utils import is_valid_email, is_valid_password
from app.services.gcp_setup import update_cloud_run_service
from app.services.settings import reload_overrides, set_setting

setup_bp = Blueprint("setup", __name__)

_INFRA_SETUP_KEYS = {
    "DATABASE_URL",
    "SECRET_KEY",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_OPTIONS",
    "CLOUD_SQL_CONNECTION_NAME",
}


def _normalize_config_value(value: Any) -> str:
    """Return a trimmed string value for setup configuration reads.

    Args:
        value: Raw value pulled from ``current_app.config`` or other sources.

    Returns:
        Trimmed string representation of the value, or an empty string when the
        input is ``None``.
    """

    if value is None:
        return ""
    return str(value).strip()


def _get_setup_config_value(config_key: str) -> str:
    """Return the setup configuration value for a given key.

    Args:
        config_key: Configuration key to read from ``current_app.config``.

    Returns:
        Trimmed string value stored in the Flask configuration.

    External dependencies:
        * Reads from :data:`flask.current_app.config`.
    """

    return _normalize_config_value(current_app.config.get(config_key))


def _collect_env_checks() -> list[dict[str, Any]]:
    """Return environment health checks for the setup landing page.

    Returns:
        A list of dictionaries containing the variable label, a boolean
        ``configured`` flag, and instructional text for the setup templates.

    External dependencies:
        * Reads environment variables via :func:`os.getenv`.
        * Reads defaults from :data:`flask.current_app.config`.
    """

    secret_key = _get_setup_config_value("SECRET_KEY") or os.getenv("SECRET_KEY")
    maps_key = (
        os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("MAPS_API_KEY")
        or _get_setup_config_value("GOOGLE_MAPS_API_KEY")
    )
    gcs_bucket = _get_setup_config_value("GCS_BUCKET") or os.getenv("GCS_BUCKET")
    database_url = _get_setup_config_value("DATABASE_URL") or os.getenv("DATABASE_URL")
    cloud_sql_connection = _get_setup_config_value(
        "CLOUD_SQL_CONNECTION_NAME"
    ) or os.getenv("CLOUD_SQL_CONNECTION_NAME")
    postgres_user = _get_setup_config_value("POSTGRES_USER") or os.getenv(
        "POSTGRES_USER"
    )
    postgres_password = _get_setup_config_value("POSTGRES_PASSWORD") or os.getenv(
        "POSTGRES_PASSWORD"
    )
    postgres_db = _get_setup_config_value("POSTGRES_DB") or os.getenv("POSTGRES_DB")
    postgres_host = _get_setup_config_value("POSTGRES_HOST") or os.getenv(
        "POSTGRES_HOST"
    )
    postgres_port = _get_setup_config_value("POSTGRES_PORT") or os.getenv(
        "POSTGRES_PORT"
    )
    postgres_options = _get_setup_config_value("POSTGRES_OPTIONS") or os.getenv(
        "POSTGRES_OPTIONS"
    )
    database_configured = bool(database_url) or (
        bool(postgres_password)
        and bool(postgres_db)
        and bool(postgres_user)
        and (bool(postgres_host) or bool(cloud_sql_connection))
    )

    return [
        {
            "label": "SECRET_KEY",
            "configured": bool(secret_key),
            "instruction": (
                "Set SECRET_KEY to a long random value so session cookies are secure."
            ),
        },
        {
            "label": "GOOGLE_MAPS_API_KEY or MAPS_API_KEY",
            "configured": bool(maps_key),
            "instruction": (
                "Provide a Google Maps API key so ZIP validation and distance lookups work."
            ),
        },
        {
            "label": "GCS_BUCKET",
            "configured": bool(gcs_bucket),
            "instruction": (
                "Set GCS_BUCKET when branding assets should be stored in Google Cloud Storage."
            ),
        },
        {
            "label": "DATABASE_URL or POSTGRES_* connection variables",
            "configured": database_configured,
            "instruction": (
                "Set DATABASE_URL (recommended) or provide POSTGRES_USER, "
                "POSTGRES_PASSWORD, POSTGRES_DB, and POSTGRES_HOST or "
                "CLOUD_SQL_CONNECTION_NAME. Add POSTGRES_PORT and "
                "POSTGRES_OPTIONS as needed."
            ),
        },
    ]


def _get_setup_override_fields() -> list[dict[str, Any]]:
    """Return metadata for the setup configuration form.

    Returns:
        A list of dictionaries describing each configurable field for the setup
        landing page. Each dictionary includes the field name, configuration
        key, input type, and help text for the UI.

    External dependencies:
        * Reads :data:`flask.current_app.config` for prefilled values.
    """

    field_specs = [
        {
            "name": "secret_key",
            "config_key": "SECRET_KEY",
            "label": "SECRET_KEY",
            "is_secret": True,
            "help_text": (
                "Paste a long random value to secure sessions. Rotating this key "
                "signs users out."
            ),
        },
        {
            "name": "google_maps_api_key",
            "config_key": "GOOGLE_MAPS_API_KEY",
            "label": "GOOGLE_MAPS_API_KEY",
            "is_secret": True,
            "help_text": (
                "Required for ZIP validation and distance lookups. Leave blank to "
                "keep the current value."
            ),
        },
        {
            "name": "gcs_bucket",
            "config_key": "GCS_BUCKET",
            "label": "GCS_BUCKET",
            "is_secret": False,
            "help_text": (
                "Provide the target Google Cloud Storage bucket for branding assets."
            ),
        },
        {
            "name": "database_url",
            "config_key": "DATABASE_URL",
            "label": "DATABASE_URL",
            "is_secret": True,
            "help_text": (
                "Recommended: provide a full PostgreSQL DSN. Save this value and "
                "restart the app to reinitialize the database connection."
            ),
        },
        {
            "name": "postgres_user",
            "config_key": "POSTGRES_USER",
            "label": "POSTGRES_USER",
            "is_secret": False,
            "help_text": (
                "Database username used when building a Postgres connection. "
                "Restart the app after saving."
            ),
        },
        {
            "name": "postgres_password",
            "config_key": "POSTGRES_PASSWORD",
            "label": "POSTGRES_PASSWORD",
            "is_secret": True,
            "help_text": (
                "Database password used when building a Postgres connection. "
                "Restart the app after saving."
            ),
        },
        {
            "name": "postgres_db",
            "config_key": "POSTGRES_DB",
            "label": "POSTGRES_DB",
            "is_secret": False,
            "help_text": (
                "Database name used when building a Postgres connection. "
                "Restart the app after saving."
            ),
        },
        {
            "name": "postgres_host",
            "config_key": "POSTGRES_HOST",
            "label": "POSTGRES_HOST",
            "is_secret": False,
            "help_text": (
                "Database host or IP for TCP connections. Restart the app after saving."
            ),
        },
        {
            "name": "postgres_port",
            "config_key": "POSTGRES_PORT",
            "label": "POSTGRES_PORT",
            "is_secret": False,
            "help_text": (
                "Database port for TCP connections (typically 5432). Restart the app "
                "after saving."
            ),
        },
        {
            "name": "postgres_options",
            "config_key": "POSTGRES_OPTIONS",
            "label": "POSTGRES_OPTIONS",
            "is_secret": False,
            "help_text": (
                "Optional query-string settings like sslmode=require. Restart the app "
                "after saving."
            ),
        },
        {
            "name": "cloud_sql_connection_name",
            "config_key": "CLOUD_SQL_CONNECTION_NAME",
            "label": "CLOUD_SQL_CONNECTION_NAME",
            "is_secret": False,
            "help_text": (
                "Cloud SQL connection name for Unix socket connections. Restart the "
                "app after saving."
            ),
        },
    ]

    fields: list[dict[str, Any]] = []
    for field in field_specs:
        config_key = field["config_key"]
        current_value = _get_setup_config_value(config_key)
        fields.append(
            {
                **field,
                "input_type": field.get(
                    "input_type", "password" if field["is_secret"] else "text"
                ),
                "value": "" if field["is_secret"] else current_value,
                "configured": bool(current_value),
            }
        )
    return fields


def _persist_setup_overrides(
    form_data: Mapping[str, str],
) -> tuple[list[str], list[str], bool]:
    """Persist setup overrides from the setup checklist form.

    Args:
        form_data: Submitted form payload containing configuration values.

    Returns:
        A tuple containing the list of updated configuration keys, a list of
        error messages to display in the UI, and a boolean indicating whether
        Cloud Run was updated with new infra settings.

    External dependencies:
        * Calls :func:`app.services.gcp_setup.update_cloud_run_service` to update
          infra settings on Cloud Run.
        * Calls :func:`app.services.settings.set_setting` to save overrides.
        * Calls :func:`app.services.settings.reload_overrides` to refresh
          ``current_app.config``.
    """

    updated_keys: list[str] = []
    errors: list[str] = []
    cloud_run_updated = False
    app_fields, infra_fields = _split_setup_overrides(form_data)
    use_cloud_run = bool(os.environ.get("K_SERVICE"))

    if use_cloud_run and infra_fields:
        infra_values = {
            field["config_key"]: field["value"] for field in infra_fields
        }
        try:
            update_cloud_run_service(infra_values)
        except (RuntimeError, ValueError, ImportError) as exc:
            current_app.logger.exception(
                "Cloud Run update failed for %s: %s",
                ", ".join(sorted(infra_values.keys())),
                exc,
            )
            errors.append(
                "Unable to update Cloud Run settings. Check the logs for details."
            )
        else:
            updated_keys.extend(sorted(infra_values.keys()))
            cloud_run_updated = True
    elif infra_fields:
        app_fields.extend(infra_fields)

    db_updates_made = False
    for field in app_fields:
        try:
            set_setting(
                field["config_key"],
                field["value"],
                is_secret=bool(field["is_secret"]),
            )
        except (OperationalError, ProgrammingError) as exc:
            current_app.logger.exception(
                "Setup override save failed for %s: %s", field["config_key"], exc
            )
            errors.append(
                f"Unable to save {field['label']}. Confirm the database is ready."
            )
        else:
            updated_keys.append(field["config_key"])
            db_updates_made = True

    if db_updates_made:
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            current_app.logger.exception("Setup override commit failed: %s", exc)
            errors.append("Unable to save settings. Check the database connection.")
            updated_keys = [key for key in updated_keys if key in _INFRA_SETUP_KEYS]
        else:
            reload_overrides(current_app)

    return updated_keys, errors, cloud_run_updated


def _split_setup_overrides(
    form_data: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split setup overrides into app-only and infrastructure settings.

    Args:
        form_data: Submitted form payload containing configuration values.

    Returns:
        A tuple containing a list of app-only override field dictionaries and a
        list of infrastructure override field dictionaries. Each dictionary
        includes the ``config_key``, ``label``, ``is_secret`` flag, and cleaned
        ``value``.

    External dependencies:
        * Calls :func:`_get_setup_override_fields` to load form definitions.
    """

    app_fields: list[dict[str, Any]] = []
    infra_fields: list[dict[str, Any]] = []
    for field in _get_setup_override_fields():
        raw_value = (form_data.get(field["name"]) or "").strip()
        if not raw_value:
            continue
        entry = {
            "config_key": field["config_key"],
            "label": field["label"],
            "is_secret": field["is_secret"],
            "value": raw_value,
        }
        if field["config_key"] in _INFRA_SETUP_KEYS:
            infra_fields.append(entry)
        else:
            app_fields.append(entry)

    return app_fields, infra_fields


@setup_bp.route("/setup", methods=["GET", "POST"])
def setup_status() -> str:
    """Render a setup checklist for required environment configuration.

    Inputs:
        Reads form data from :data:`flask.request.form` when the request method
        is ``POST``.

    Returns:
        Rendered HTML for the setup landing page, or a restart notice when
        Cloud Run settings are being applied.

    External dependencies:
        * Calls :func:`_persist_setup_overrides` to save submitted values.
        * Calls :func:`flask.flash` to surface status messages to the UI.
    """

    if request.method == "POST":
        updated_keys, errors, cloud_run_updated = _persist_setup_overrides(
            request.form
        )
        if errors:
            for message in errors:
                flash(message, "danger")
        if updated_keys:
            flash(
                "Saved configuration overrides: " + ", ".join(sorted(updated_keys)),
                "success",
            )
        elif not errors:
            flash("No configuration values were provided.", "info")
        if cloud_run_updated:
            return render_template(
                "setup/restarting.html",
                health_url=url_for("healthz"),
                setup_url=url_for("setup.setup_status"),
            )
        return redirect(url_for("setup.setup_status"))

    checks = _collect_env_checks()
    missing = [check for check in checks if not check["configured"]]
    override_fields = _get_setup_override_fields()
    return render_template(
        "setup/index.html",
        checks=checks,
        missing=missing,
        override_fields=override_fields,
    )


@setup_bp.route("/setup/db-init", methods=["GET", "POST"])
def setup_db_init() -> ResponseReturnValue:
    """Initialize or upgrade the database schema for first-run setup.

    Returns:
        ``ResponseReturnValue`` that renders the DB init template or redirects
        back to it after invoking :func:`app.database.ensure_database_schema`.

    External dependencies:
        * Calls :func:`app.database.ensure_database_schema` to run migrations or
          create tables for SQLite databases.
    """

    if request.method == "POST":
        try:
            ensure_database_schema(db.engine)
        except Exception as exc:  # pragma: no cover - depends on database state
            current_app.logger.exception("Setup database init failed: %s", exc)
            flash("Database initialization failed. Check logs for details.", "danger")
        else:
            flash("Database initialization completed.", "success")
        return redirect(url_for("setup.setup_db_init"))

    return render_template("setup/db_init.html")


@setup_bp.route("/setup/admin", methods=["GET", "POST"])
def setup_admin() -> ResponseReturnValue:
    """Collect credentials and create the initial super administrator.

    Returns:
        ``ResponseReturnValue`` rendering the admin form or redirecting to the
        setup completion route when the administrator is created.

    External dependencies:
        * Calls :func:`app.services.auth_utils.is_valid_email` and
          :func:`app.services.auth_utils.is_valid_password` for validation.
        * Stores passwords using :meth:`app.models.User.set_password`.
    """

    if User.query.count() > 0:
        flash("Setup is already complete. Please sign in.", "info")
        return redirect(url_for("auth.login"))

    email_value = (request.form.get("email") or "").strip().lower()
    if request.method == "POST":
        password = request.form.get("password") or ""
        errors: list[str] = []

        if not email_value:
            errors.append("Email is required.")
        elif not is_valid_email(email_value):
            errors.append("Enter a valid email address.")

        if not is_valid_password(password):
            errors.append(
                "Password must be at least 14 characters and include upper, lower, "
                "number, and symbol characters (or 24+ characters for a passphrase)."
            )

        if User.query.filter_by(email=email_value).first():
            errors.append("Email already exists in the database.")

        if errors:
            for error in errors:
                flash(error, "warning")
        else:
            user = User(
                email=email_value,
                role="super_admin",
                employee_approved=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("Super admin account created.", "success")
            return redirect(url_for("setup.setup_complete"))

    return render_template("setup/admin.html", email=email_value)


@setup_bp.route("/setup/complete", methods=["GET"])
def setup_complete() -> ResponseReturnValue:
    """Mark setup complete and redirect to the admin dashboard.

    Returns:
        ``ResponseReturnValue`` redirecting the caller to
        :func:`admin.dashboard`.
    """

    current_app.config["SETUP_REQUIRED"] = False
    return redirect(url_for("admin.dashboard"))
