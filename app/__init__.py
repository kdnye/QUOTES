# app/__init__.py
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import LoginManager, current_user, login_required
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from markupsafe import Markup
from datetime import datetime
import os
from jinja2 import TemplateNotFound
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from typing import List, Optional, Tuple, Union
from flask.typing import ResponseReturnValue
from flask_session import Session as FlaskSession
import redis as redispy

from app.quote.distance import get_distance_miles
from app.quote.theme import init_fsi_theme
from .models import db, User, Quote, HotshotRate
from app.services.mail import (
    MailRateLimitError,
    enforce_mail_rate_limit,
    send_email,
    validate_sender_domain,
    user_has_mail_privileges,
)
from app.services.settings import reload_overrides
from app.services.oidc_client import init_oidc_oauth

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)
TRUTHY_VALUES = {"1", "true", "t", "yes", "y", "on"}
DEFAULT_HEALTHCHECK_DB_TIMEOUT = 2.0
PRODUCTION_ENV_VALUES = {"production", "prod", "live"}


def _is_truthy(value: Optional[Union[str, bool]]) -> bool:
    """Return whether a string or boolean represents a truthy value.

    Args:
        value: Raw value from configuration or environment variables.

    Returns:
        ``True`` when the provided value maps to a truthy string or ``True``.
    """

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in TRUTHY_VALUES


def _is_production_environment() -> bool:
    """Return whether the current environment is marked as production.

    Returns:
        ``True`` when ``ENVIRONMENT`` or ``FLASK_ENV`` resolves to a production
        value, otherwise ``False``.

    External dependencies:
        * Reads environment variables via :func:`os.getenv`.
    """

    raw_environment = os.getenv("ENVIRONMENT") or os.getenv("FLASK_ENV") or ""
    normalized = raw_environment.strip().lower()
    return normalized in PRODUCTION_ENV_VALUES


def _should_show_config_errors(app: Flask) -> bool:
    """Return whether configuration errors may be displayed to operators.

    Args:
        app: Flask application used to read configuration defaults.

    Returns:
        ``True`` when configuration errors should be exposed to operators.

    External dependencies:
        * Reads ``SHOW_CONFIG_ERRORS`` from :func:`os.getenv`.
        * Calls :func:`_is_truthy` to interpret the flag.
        * Calls :func:`_is_production_environment` to detect production.
    """

    raw_flag = os.getenv("SHOW_CONFIG_ERRORS")
    if raw_flag is None:
        raw_flag = app.config.get("SHOW_CONFIG_ERRORS")
    if _is_truthy(raw_flag):
        return True
    return not _is_production_environment()


def _coerce_timeout_seconds(
    value: Optional[Union[str, float, int]],
    default: float = DEFAULT_HEALTHCHECK_DB_TIMEOUT,
) -> float:
    """Return a positive timeout value in seconds.

    Args:
        value: Raw timeout input (string, float, or integer).
        default: Fallback used when parsing fails or the value is not positive.

    Returns:
        Timeout value in seconds.
    """

    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _resolve_healthcheck_db_settings(app: Flask) -> Tuple[bool, float]:
    """Return the database health check flag and timeout.

    Args:
        app: Application instance to read configuration defaults from.

    Returns:
        Tuple of ``(require_db_check, timeout_seconds)``.

    External dependencies:
        * Reads environment variables via :func:`os.getenv`.
    """

    raw_flag = os.getenv("HEALTHCHECK_REQUIRE_DB")
    if raw_flag is None:
        raw_flag = app.config.get("HEALTHCHECK_REQUIRE_DB")
    require_db = _is_truthy(raw_flag)

    raw_timeout = os.getenv("HEALTHCHECK_DB_TIMEOUT_SECONDS")
    if raw_timeout is None:
        raw_timeout = app.config.get("HEALTHCHECK_DB_TIMEOUT_SECONDS")
    timeout_seconds = _coerce_timeout_seconds(raw_timeout)
    return require_db, timeout_seconds


def _check_database_connectivity(timeout_seconds: float) -> bool:
    """Return whether the database responds to a lightweight query.

    Args:
        timeout_seconds: Maximum time to allow the query to run.

    Returns:
        ``True`` when the database responds successfully, otherwise ``False``.

    External dependencies:
        * Uses :mod:`app.models.db` for SQLAlchemy engine connectivity.
        * Executes SQL via :func:`sqlalchemy.text`.
    """

    timeout_ms = max(int(timeout_seconds * 1000), 1)
    try:
        with db.engine.connect() as connection:
            with connection.begin():
                if connection.dialect.name == "postgresql":
                    connection.execute(
                        text("SET LOCAL statement_timeout = :timeout_ms"),
                        {"timeout_ms": timeout_ms},
                    )
                connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - depends on database state
        current_app.logger.warning("Health check database connectivity failed: %s", exc)
        return False


def _should_run_startup_db_checks(app: Flask, config_errors: List[str]) -> bool:
    """Return whether database migrations and inspection should run at startup.

    Args:
        app: Application instance used to read configuration defaults.
        config_errors: Startup configuration errors collected by ``Config``.

    Returns:
        ``True`` when startup database checks should run, ``False`` when they
        should be skipped.

    External dependencies:
        * Reads environment variables via :func:`os.getenv`.
    """

    raw_setting = os.getenv("STARTUP_DB_CHECKS")
    if raw_setting is None:
        raw_setting = app.config.get("STARTUP_DB_CHECKS")
    if raw_setting is not None:
        return _is_truthy(raw_setting)
    if config_errors:
        return False
    return True


def _is_setup_required() -> bool:
    """Return whether the first-run setup flow should be enforced.

    Returns:
        ``True`` when no :class:`app.models.User` records exist, ``False`` when
        a user is present, and ``False`` when database errors prevent
        validation. When database errors occur, sets
        ``SETUP_VALIDATION_FAILED`` on the active app configuration.

    External dependencies:
        * Uses :class:`app.models.User` and ``User.query.count`` to inspect the
          database.
        * Logs warnings via :attr:`flask.current_app.logger` when database
          errors occur.
    """

    try:
        return User.query.count() == 0
    except (OperationalError, SQLAlchemyError) as exc:
        current_app.logger.warning(
            "Setup validation skipped due to database error: %s", exc
        )
        current_app.config["SETUP_VALIDATION_FAILED"] = True
        return False


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def build_map_html(origin_zip: str, destination_zip: str) -> Optional[str]:
    """Return an embedded Google Maps iframe for the given ZIP codes.

    Returns ``None`` if the API key is missing or the ZIPs are invalid.
    """
    key = current_app.config.get("GOOGLE_MAPS_API_KEY") or os.getenv(
        "GOOGLE_MAPS_API_KEY"
    )
    if not key:
        return None

    o = "".join(ch for ch in str(origin_zip).strip() if ch.isdigit())
    d = "".join(ch for ch in str(destination_zip).strip() if ch.isdigit())
    if len(o) != 5 or len(d) != 5:
        return None

    src = (
        "https://www.google.com/maps/embed/v1/directions"
        f"?key={key}&origin={o},USA&destination={d},USA"
    )
    return (
        '<iframe width="600" height="450" style="border:0" '
        f'loading="lazy" allowfullscreen src="{src}"></iframe>'
    )


def _verify_app_setup(app: Flask) -> List[str]:
    """Check for required database tables and templates.

    Args:
        app: The active :class:`~flask.Flask` application.

    Returns:
        A list of human-readable error messages describing missing
        resources. Uses :func:`sqlalchemy.inspect` to inspect database
        tables and :mod:`jinja2` to look up templates.

    External dependencies:
        * Calls :func:`sqlalchemy.inspect` on :data:`app.models.db.engine`.
        * Uses :mod:`jinja2` to verify required templates are available.
        * Logs database inspection errors via :attr:`flask.Flask.logger`.
    """
    errors: List[str] = []
    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
    except Exception as exc:  # pragma: no cover - depends on runtime database
        app.logger.warning("Startup database inspection failed: %s", exc)
        errors.append("Database unavailable; skipping table checks.")
    else:
        required_tables = {
            User.__tablename__,
            Quote.__tablename__,
            HotshotRate.__tablename__,
        }
        for table in required_tables:
            if table not in existing_tables:
                app.logger.error(f"SETUP_ERROR: Missing table: {table}")
                errors.append(f"Missing table: {table}")

    required_templates = ["index.html", "map.html", "new_quote.html", "500.html"]
    for tmpl in required_templates:
        try:
            app.jinja_env.get_or_select_template(tmpl)
        except TemplateNotFound:
            errors.append(f"Missing template: {tmpl}")

    return errors


def create_app(config_class: Union[str, type] = "config.Config") -> Flask:
    """Application factory for the quote tool.

    Args:
        config_class: Import path or class used to configure the app.

    Returns:
        A fully initialized :class:`~flask.Flask` application.

    External dependencies:
        * Initializes Flask extensions (SQLAlchemy, CSRF, sessions) via
          ``app.models.db`` and :class:`flask_wtf.CSRFProtect`.
        * Calls :func:`app.database.ensure_database_schema` to provision tables
          when migrations are enabled and startup checks are not disabled. Any
          migration failures are captured so the app can start in maintenance
          mode.
        * Computes ``SETUP_REQUIRED`` based on :func:`_is_setup_required` so the
          setup flow can redirect until an administrator account exists. When
          database validation fails, setup errors are recorded so the app starts
          in maintenance mode.
        * Loads override settings via :func:`app.services.settings.reload_overrides`.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(config_class)

    # # Configure file-based logging
    # log_file = "/tmp/app.log"
    # file_handler = RotatingFileHandler(
    #     log_file, maxBytes=1024 * 1024 * 10, backupCount=5
    # )
    # file_handler.setLevel(logging.INFO)
    # formatter = logging.Formatter(
    #     "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    # )
    # file_handler.setFormatter(formatter)
    # app.logger.addHandler(file_handler)

    raw_config_errors = list(app.config.get("CONFIG_ERRORS", []))
    show_config_errors = _should_show_config_errors(app)
    app.config["SHOW_CONFIG_ERRORS"] = show_config_errors
    app.config["CONFIG_ERROR_DETAILS"] = raw_config_errors
    if raw_config_errors:
        app.logger.error("CONFIG_ERRORS: %s", raw_config_errors)
    config_errors = raw_config_errors
    if config_errors and not _is_production_environment():
        app.logger.info(
            "Ignoring startup configuration errors because ENVIRONMENT/FLASK_ENV "
            "is not production."
        )
        config_errors = []

    # Initialize optional server-side sessions (Redis) when configured.
    sess_type = os.getenv("SESSION_TYPE", app.config.get("SESSION_TYPE"))
    if sess_type and sess_type.lower() == "redis":
        sess_redis_url = os.getenv("SESSION_REDIS", app.config.get("SESSION_REDIS"))
        if sess_redis_url:
            try:
                redis_conn = redispy.from_url(sess_redis_url)
                app.config["SESSION_TYPE"] = "redis"
                app.config["SESSION_REDIS"] = redis_conn
                FlaskSession(app)
                app.logger.info("Flask server-side sessions enabled via Redis.")
            except Exception as exc:  # pragma: no cover - operational fallback
                app.logger.exception(
                    "Redis session init failed, falling back to cookie sessions: %s",
                    exc,
                )

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    init_fsi_theme(app)
    init_oidc_oauth(app)

    # Ensure database tables exist before handling requests.
    from app.database import (
        ensure_database_schema,
    )  # Local import avoids circular imports.

    with app.app_context():
        # Run migrations/schema creation when explicitly enabled.
        migrate_enabled = os.getenv("MIGRATE_ON_STARTUP", "false").strip().lower() in (
            "1",
            "true",
            "t",
            "yes",
            "y",
            "on",
        )
        run_db_checks = _should_run_startup_db_checks(app, config_errors)
        setup_errors: List[str] = []
        non_config_errors: List[str] = []
        if run_db_checks:
            if migrate_enabled:
                try:
                    ensure_database_schema(db.engine)
                except Exception as exc:  # pragma: no cover - depends on database
                    app.logger.warning("Startup database migration failed: %s", exc)
                    non_config_errors.append(
                        "Database unavailable; skipping migrations."
                    )
            non_config_errors.extend(_verify_app_setup(app))
        else:
            if config_errors:
                app.logger.warning(
                    "Skipping startup database checks due to configuration errors."
                )
            else:
                app.logger.info(
                    "Skipping startup database checks because STARTUP_DB_CHECKS is "
                    "disabled."
                )
        reload_overrides(app)
        try:
            app.config["SETUP_REQUIRED"] = _is_setup_required()
            if app.config.pop("SETUP_VALIDATION_FAILED", False):
                non_config_errors.append(
                    "Unable to validate setup status due to database errors."
                )
        except Exception as exc:  # pragma: no cover - defensive guard
            app.logger.warning(
                "Setup validation failed; enabling maintenance mode: %s", exc
            )
            non_config_errors.append(
                "Unable to validate setup status due to database errors."
            )
            app.config["SETUP_REQUIRED"] = False

        if config_errors:
            setup_errors.extend(config_errors)
        if non_config_errors:
            setup_errors.extend(non_config_errors)

    has_config_errors = bool(config_errors)
    has_non_config_errors = bool(non_config_errors)

    limiter.init_app(app)

    if setup_errors:
        message = "; ".join(setup_errors)
        app.logger.error("Application setup failed: %s", message)

        @app.before_request
        def _setup_failed() -> Optional[ResponseReturnValue]:
            """Return a maintenance response when startup validation fails.

            Inputs:
                None. Reads request context for path inspection.

            Returns:
                ``None`` when diagnostics should proceed, otherwise a rendered
                error response.

            External dependencies:
                * Reads :data:`flask.request.path` to allow diagnostics access.
                * Redirects to :func:`setup.setup_status` when only
                  configuration errors exist.
                * Renders :mod:`templates/500.html` via
                  :func:`flask.render_template` when non-configuration errors
                  are present.
            """

            if request.path == "/healthz/config":
                return None
            if has_config_errors and not has_non_config_errors:
                if request.path.startswith("/setup"):
                    return None
                return redirect(url_for("setup.setup_status"))
            error_details = raw_config_errors if show_config_errors else None
            return (
                render_template(
                    "500.html",
                    message="Application is misconfigured.",
                    error_details=error_details,
                ),
                500,
            )

    def _is_allowed_setup_path(path: str) -> bool:
        """Return True when ``path`` should bypass setup redirects.

        Args:
            path: Raw request path from :data:`flask.request.path`.

        Returns:
            ``True`` when setup enforcement should skip the path.
        """

        return (
            path.startswith("/setup")
            or path.startswith("/static")
            or path == "/healthz"
            or path == "/healthz/config"
        )

    @app.before_request
    def _redirect_to_setup() -> Optional[ResponseReturnValue]:
        """Redirect to setup when no users exist in the database.

        Returns:
            ``None`` when the request should continue as normal. When setup is
            required, returns a redirect response to
            :func:`setup.setup_status`.

        External dependencies:
            * Calls :func:`_is_setup_required`, which queries
              :class:`app.models.User`.
        """

        if _is_allowed_setup_path(request.path):
            return None
        setup_required = _is_setup_required()
        current_app.config["SETUP_REQUIRED"] = setup_required
        if setup_required:
            return redirect(url_for("setup.setup_status"))
        return None

    # Blueprints
    from app.api import api_bp
    from .auth import auth_bp
    from .admin import admin_bp
    from .help import help_bp
    from app.setup import setup_bp
    from .quotes import quotes_bp
    from app.quote.admin_view import admin_quotes_bp

    csrf.exempt(api_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(admin_quotes_bp, url_prefix="/admin")
    app.register_blueprint(quotes_bp, url_prefix="/quotes")
    app.register_blueprint(help_bp, url_prefix="/help")
    app.register_blueprint(setup_bp)

    @app.route("/", methods=["GET"])
    def index() -> str:
        """Display a landing page explaining login requirements."""
        return render_template("index.html")

    @app.route("/healthz", methods=["GET"])
    def healthz() -> ResponseReturnValue:
        """Return a lightweight health response for infrastructure probes.

        Inputs:
            Reads ``HEALTHCHECK_REQUIRE_DB`` and
            ``HEALTHCHECK_DB_TIMEOUT_SECONDS`` from the environment or app
            configuration.

        Returns:
            ``("ok", 200)`` when healthy or ``("db unavailable", 500)`` when the
            database connectivity check fails.

        External dependencies:
            * Calls :func:`_check_database_connectivity`, which uses
              :mod:`app.models.db` for database access.
        """

        require_db, timeout_seconds = _resolve_healthcheck_db_settings(app)
        if require_db and not _check_database_connectivity(timeout_seconds):
            return "db unavailable", 500
        return "ok", 200

    @app.route("/healthz/config", methods=["GET"])
    def healthz_config() -> ResponseReturnValue:
        """Return configuration error diagnostics when explicitly allowed.

        Inputs:
            None. Uses the current request context and application config.

        Returns:
            A JSON payload with configuration errors when diagnostics are
            enabled, otherwise a 404 response to avoid exposing secrets.

        External dependencies:
            * Reads :attr:`flask.Flask.config` to determine diagnostic access.
            * Uses :func:`flask.jsonify` to format the response.
        """

        if not show_config_errors:
            abort(404)
        return jsonify({"errors": raw_config_errors})

    @app.route("/map", methods=["POST"])
    def map_view():
        origin_zip = (request.form.get("origin_zip") or "").strip()
        dest_zip = (request.form.get("destination_zip") or "").strip()
        html = build_map_html(origin_zip, dest_zip)
        if html is None:
            flash("Could not locate one or both ZIP codes.", "warning")
            return redirect(url_for("index"))
        return render_template("map.html", map_html=Markup(html))

    @app.route("/send", methods=["POST"])
    @login_required
    def send_email_route() -> ResponseReturnValue:
        """Send a quote summary email on behalf of an authenticated user.

        Inputs:
            origin_zip: ZIP code for the quote origin provided via form data.
            destination_zip: ZIP code for the quote destination provided via form data.
            email: Recipient email address submitted in the POST body.

        Returns:
            A redirect response back to :func:`quotes.new_quote` or
            :func:`index`, depending on validation outcomes.

        External dependencies:
            * :func:`services.mail.user_has_mail_privileges` to restrict usage
              to Freight Services staff accounts.
            * :func:`send_email` for the actual SMTP dispatch.
        """
        origin_zip = (request.form.get("origin_zip") or "").strip()
        dest_zip = (request.form.get("destination_zip") or "").strip()
        email = (request.form.get("email") or "").strip()

        if not user_has_mail_privileges(current_user):
            flash(
                "Quote emails are limited to Freight Services staff accounts.",
                "warning",
            )
            return redirect(url_for("quotes.new_quote"))

        if not email:
            flash("Recipient email is required to send a quote.", "warning")
            return redirect(url_for("index"))

        miles = get_distance_miles(origin_zip, dest_zip)
        miles_text = f"{miles:,.2f} miles" if miles is not None else "N/A"

        subject = f"Quote for {origin_zip} \u2192 {dest_zip}"
        body = (
            "Quote Details\n\n"
            f"Origin ZIP: {origin_zip}\n"
            f"Destination ZIP: {dest_zip}\n"
            f"Estimated Distance: {miles_text}\n"
            f"Generated: {datetime.utcnow().isoformat()}Z\n"
        )

        try:
            if os.getenv("CELERY_BROKER_URL"):
                try:
                    from worker.email_tasks import send_email_task

                    uid = (
                        current_user.get_id()
                        if getattr(current_user, "is_authenticated", False)
                        else None
                    )
                    send_email_task.delay(email, subject, body, "quote_email", uid)
                    flash("Quote email queued for delivery.", "success")
                except Exception as exc:  # pragma: no cover - guarded fallback
                    current_app.logger.exception("Failed to queue email task: %s", exc)
                    send_email(
                        email,
                        subject,
                        body,
                        feature="quote_email",
                        user=current_user,
                    )
                    flash("Quote email sent (fallback).", "success")
            else:
                send_email(
                    email,
                    subject,
                    body,
                    feature="quote_email",
                    user=current_user,
                )
                flash("Quote email sent.", "success")
        except MailRateLimitError as exc:
            flash(str(exc), "warning")
        except Exception as e:
            current_app.logger.exception("Email send failed: %s", e)
            flash("Failed to send email. Check SMTP settings.", "danger")

        return redirect(url_for("index"))

    return app
