"""Central configuration for the Quote Tool Flask application.

The module resolves the application's base directory, ensures the default
SQLite instance folder exists, and exposes the :class:`Config` settings class.

Key settings exposed by :class:`Config`:

* ``SECRET_KEY``: Secures Flask sessions and form submissions. Generated at
  startup when the ``SECRET_KEY`` environment variable is missing in
  development and test environments so each deployment receives a unique value.
* ``SQLALCHEMY_DATABASE_URI`` and ``SQLALCHEMY_ENGINE_OPTIONS``: Provide the
  SQLAlchemy connection string and optional connection pooling behaviour.
* ``DB_POOL_RECYCLE`` and ``DB_POOL_MAX_OVERFLOW``: Tune SQLAlchemy connection
  recycling to avoid Cloud Run idle timeouts and control burst capacity.
* ``GOOGLE_MAPS_API_KEY``: Supplies credentials for distance calculations and
  address lookups.
* ``CACHE_TYPE`` and ``CACHE_REDIS_URL``: Configure the caching backend.
* ``MAIL_*`` fields: Enable optional outbound mail integration for password
  resets and notifications.
* ``RATELIMIT_*`` and ``AUTH_*_RATE_LIMIT``: Configure global and endpoint
  rate limiting enforced by :mod:`flask_limiter`.
* ``API_AUTH_TOKEN`` and ``API_QUOTE_RATE_LIMIT``: Configure authentication
  and rate limiting for the JSON API endpoints.
* ``WTF_CSRF_ENABLED``: Toggles CSRF protection across forms.
* ``BRANDING_STORAGE``, ``GCS_BUCKET``, and ``GCS_PREFIX``: Configure branding
  logo storage for local or Google Cloud Storage backends.

All values default to development-friendly settings and can be overridden via
environment variables so each deployment can customize behaviour without
modifying code.
"""

# config.py
import logging
import os
import socket
from pathlib import Path
from secrets import token_urlsafe
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "instance" / "app.db"
# Capture configuration errors so the application can start in a safe
# maintenance mode instead of crashing during import time.
_CONFIG_ERRORS: List[str] = []


def _record_startup_error(message: str) -> None:
    """Record a configuration error that should block normal startup.

    Args:
        message: Human-readable description of the configuration failure.

    Returns:
        ``None``. Adds the message to the module-level error list and logs it.

    External Dependencies:
        Logs via :mod:`logging` to the ``quote_tool.config`` logger.
    """

    logging.getLogger("quote_tool.config").error(message)
    _CONFIG_ERRORS.append(message)


# Ensure the default database directory exists so all tools share the same DB.
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _is_production_environment() -> bool:
    """Return ``True`` when the configured runtime environment is production.

    The helper checks ``ENVIRONMENT`` first and then ``FLASK_ENV`` to align with
    Flask's built-in environment indicator. It treats ``production``, ``prod``,
    and ``live`` as production values after lowercasing the input.

    Returns:
        bool: ``True`` when the environment indicates production, ``False``
        otherwise.

    External Dependencies:
        Calls :func:`os.getenv` to read ``ENVIRONMENT`` and ``FLASK_ENV`` from
        the process environment.
    """

    raw_environment = os.getenv("ENVIRONMENT") or os.getenv("FLASK_ENV") or ""
    normalized = raw_environment.strip().lower()
    return normalized in {"production", "prod", "live"}


def _resolve_secret_key() -> str:
    """Return a cryptographically strong secret key for Flask sessions.

    Returns:
        str: Configured ``SECRET_KEY`` value or a generated token for
        development/test environments. In production, missing keys are recorded
        as startup errors and a temporary key is generated so the app can run
        in maintenance mode.

    External Dependencies:
        Calls :func:`os.getenv` to read ``SECRET_KEY`` and environment flags.
        Uses :func:`secrets.token_urlsafe` to generate a fallback value.
        Logs warnings via :func:`logging.getLogger`.
    """

    configured = os.getenv("SECRET_KEY")
    if configured:
        return configured

    if _is_production_environment():
        _record_startup_error(
            "SECRET_KEY must be set when ENVIRONMENT or FLASK_ENV indicates "
            "production."
        )
        generated = token_urlsafe(32)
        logging.getLogger("quote_tool.config").warning(
            "SECRET_KEY is missing in production; generated a temporary key "
            "so the app can start in maintenance mode."
        )
        return generated

    generated = token_urlsafe(32)
    logging.getLogger("quote_tool.config").warning(
        "SECRET_KEY environment variable is not set; generated a one-time key."
    )
    return generated


def _get_int_from_env(var_name: str, default: int) -> int:
    """Return an integer from the environment, falling back to a default.

    Args:
        var_name: Environment variable name to read.
        default: Fallback integer when the environment value is missing
            or invalid.

    Returns:
        int: Parsed integer value or the provided fallback.

    External Dependencies:
        Calls :func:`os.getenv` to read the environment variable value.
        Logs warnings via :func:`logging.getLogger` when parsing fails.
    """

    raw_value = os.getenv(var_name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return int(raw_value)
    except ValueError:
        logging.getLogger("quote_tool.config").warning(
            "Invalid %s value %r; falling back to %s.", var_name, raw_value, default
        )
        return default


def _get_optional_int_from_env(var_name: str) -> Optional[int]:
    """Return an optional integer from the environment.

    Args:
        var_name: Environment variable name to read.

    Returns:
        Optional[int]: Parsed integer when the environment value is present and
        valid, otherwise ``None``.

    External Dependencies:
        Calls :func:`os.getenv` to read the environment variable value. Logs
        warnings via :func:`logging.getLogger` when parsing fails.
    """

    raw_value = os.getenv(var_name)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        return int(raw_value)
    except ValueError:
        logging.getLogger("quote_tool.config").warning(
            "Invalid %s value %r; ignoring override.", var_name, raw_value
        )
        return None


def _parse_postgres_options(raw_options: str) -> Iterable[Tuple[str, str]]:
    """Return key/value pairs parsed from ``POSTGRES_OPTIONS``.

    ``POSTGRES_OPTIONS`` accepts a query-string-style value such as
    ``"sslmode=require&application_name=quote-tool"``. The helper uses
    :func:`urllib.parse.parse_qsl` to decode the pairs while preserving order so
    callers can feed the result directly into :func:`urllib.parse.urlencode`.

    Args:
        raw_options: Raw string supplied via ``POSTGRES_OPTIONS``.

    Returns:
        Iterable[Tuple[str, str]]: Key/value pairs suitable for constructing a
        SQLAlchemy query string.
    """

    return parse_qsl(raw_options, keep_blank_values=True)


def _build_sqlalchemy_engine_options(
    *, pool_size: Optional[int], pool_recycle: int, max_overflow: int
) -> Dict[str, Union[int, bool]]:
    """Return SQLAlchemy engine options for connection pooling.

    Args:
        pool_size: Optional base pool size from ``DB_POOL_SIZE``. When ``None``,
            SQLAlchemy's default pool size is used.
        pool_recycle: Maximum connection age in seconds from ``DB_POOL_RECYCLE``.
            Recycling connections before Cloud Run's idle timeout helps avoid
            server-side disconnects on reused connections.
        max_overflow: Extra connections beyond ``pool_size`` from
            ``DB_POOL_MAX_OVERFLOW`` to handle short traffic bursts.

    Returns:
        Dict[str, Union[int, bool]]: SQLAlchemy engine options to pass into
        :func:`sqlalchemy.create_engine`.
    """

    options: Dict[str, Union[int, bool]] = {
        "pool_pre_ping": True,
        "pool_recycle": pool_recycle,
        "max_overflow": max_overflow,
    }
    if pool_size is not None:
        options["pool_size"] = pool_size
    return options


def _is_hostname_resolvable(hostname: str) -> bool:
    """Return ``True`` when ``hostname`` resolves in the current network namespace.

    The helper defers to :func:`socket.getaddrinfo` so Compose-style DNS entries
    (for example the ``postgres`` service hostname) are considered valid. When
    resolution fails because the hostname is missing from ``/etc/hosts`` or the
    configured DNS servers, the function returns ``False`` without raising,
    allowing callers to gracefully fall back to safer defaults.

    Args:
        hostname: Hostname extracted from a PostgreSQL connection string.

    Returns:
        bool: ``True`` when the hostname can be resolved, ``False`` otherwise.

    External Dependencies:
        Calls :func:`socket.getaddrinfo` to perform the DNS lookup.
    """

    try:
        socket.getaddrinfo(hostname, None)
        return True
    except socket.gaierror:
        return hostname not in {"postgres", "localhost"}


def _sanitize_database_url(raw_url: Optional[str]) -> Optional[str]:
    """Return a safe database URL or ``None`` when the value should be ignored.

    The helper filters out PostgreSQL URLs that omit passwords to avoid the
    connection errors observed when Compose injects incomplete DSNs. Other
    schemes are returned unchanged so SQLite- or MSSQL-style URLs still work as
    expected.

    Args:
        raw_url: Optional URL sourced from ``DATABASE_URL``.

    Returns:
        Sanitized URL string or ``None`` when the input is unusable.
    """

    if not raw_url:
        return None

    parsed = urlparse(raw_url)
    if parsed.scheme.startswith("postgres") and not parsed.password:
        return None

    return raw_url


def _is_postgres_dsn(database_uri: str) -> bool:
    """Return ``True`` when ``database_uri`` points at a PostgreSQL database.

    Args:
        database_uri: SQLAlchemy database URI to inspect.

    Returns:
        bool: ``True`` when the URI uses a PostgreSQL scheme, ``False``
        otherwise.

    External Dependencies:
        Calls :func:`urllib.parse.urlparse` to inspect the URI scheme.
    """

    return urlparse(database_uri).scheme.startswith("postgres")


def build_postgres_database_uri_from_env(
    *, driver: str = "postgresql+psycopg2"
) -> Optional[str]:
    """Assemble a PostgreSQL SQLAlchemy URI based on Compose-style environment variables.

    The helper mirrors the connection string injected by ``docker compose`` in
    ``docker-compose.yml`` so that local scripts and Flask share the same
    overrides. It inspects ``POSTGRES_USER``, ``POSTGRES_PASSWORD``,
    ``POSTGRES_DB``, ``POSTGRES_HOST`` (defaulting to ``"postgres"`` for the
    Compose network), ``POSTGRES_PORT``, and optional ``POSTGRES_OPTIONS`` to
    build a connection string. When ``POSTGRES_PASSWORD`` is unset the function
    returns ``None`` so callers can fall back to the SQLite default provided by
    :class:`Config`.

    Args:
        driver: SQLAlchemy driver prefix used when constructing the URI. The
            default matches the ``psycopg2`` driver required by
            :mod:`sqlalchemy` for PostgreSQL connections.

    Returns:
        Optional[str]: Fully assembled SQLAlchemy connection string or ``None``
        when the Compose variables were incomplete or the PostgreSQL host cannot
        be resolved in the current network namespace.

    External Dependencies:
        Calls :func:`os.getenv` to read environment variables exported by
        Docker Compose or the current shell. Uses
        :func:`urllib.parse.quote_plus` and :func:`urllib.parse.urlencode` to
        safely encode credentials and query options for SQLAlchemy.
    """

    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        return None

    user = os.getenv("POSTGRES_USER", "quote_tool")
    db_name = os.getenv("POSTGRES_DB", "quote_tool")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    options = os.getenv("POSTGRES_OPTIONS", "")
    query_pairs: Iterable[Tuple[str, str]] = []

    if not _is_hostname_resolvable(host):
        return None

    if options:
        query_pairs = _parse_postgres_options(options)

    query = f"?{urlencode(list(query_pairs))}" if options else ""
    return (
        f"{driver}://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/"
        f"{quote_plus(db_name)}{query}"
    )


def build_cloud_sql_unix_socket_uri_from_env(
    *, driver: str = "postgresql+psycopg2"
) -> Optional[str]:
    """Assemble a PostgreSQL SQLAlchemy URI for a Cloud SQL Unix socket.

    Cloud Run mounts Cloud SQL sockets under ``/cloudsql``. When the
    ``CLOUD_SQL_CONNECTION_NAME`` variable is present alongside the standard
    ``POSTGRES_*`` credentials, this helper builds a SQLAlchemy URI that points
    at the Unix socket path. Optional ``POSTGRES_OPTIONS`` values are appended
    as query parameters so SSL settings or application names can be enforced.

    Args:
        driver: SQLAlchemy driver prefix used when constructing the URI. The
            default matches the ``psycopg2`` driver required by
            :mod:`sqlalchemy` for PostgreSQL connections.

    Returns:
        Optional[str]: Fully assembled SQLAlchemy connection string or ``None``
        when required environment variables are missing.

    External Dependencies:
        Calls :func:`os.getenv` to read Cloud SQL and PostgreSQL environment
        variables. Uses :func:`urllib.parse.quote_plus` and
        :func:`urllib.parse.urlencode` to encode credentials, query options, and
        the Unix socket host path for SQLAlchemy.
    """

    connection_name = os.getenv("CLOUD_SQL_CONNECTION_NAME", "").strip()
    if not connection_name:
        return None

    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        return None

    user = os.getenv("POSTGRES_USER", "quote_tool")
    db_name = os.getenv("POSTGRES_DB", "quote_tool")
    options = os.getenv("POSTGRES_OPTIONS", "")
    query_pairs: List[Tuple[str, str]] = []

    if options:
        query_pairs.extend(_parse_postgres_options(options))

    query_pairs.append(("host", f"/cloudsql/{connection_name}"))
    query = urlencode(query_pairs)
    return (
        f"{driver}://{quote_plus(user)}:{quote_plus(password)}@/"
        f"{quote_plus(db_name)}?{query}"
    )


def _read_compose_profiles() -> Set[str]:
    """Return active Docker Compose profiles extracted from ``COMPOSE_PROFILES``.

    The helper consults :func:`os.getenv` so it mirrors the environment exposed
    to the running application container. When the variable is unset the
    function returns an empty set, signalling that only the default profile is
    active. Profiles are split on commas to match the behaviour of
    ``docker compose`` documented at https://docs.docker.com/compose/profiles/.

    Returns:
        Set[str]: Normalised profile names enabled for this deployment.
    """

    raw_profiles = os.getenv("COMPOSE_PROFILES", "")
    return {profile.strip() for profile in raw_profiles.split(",") if profile.strip()}


def _resolve_cache_type() -> str:
    """Select the Flask-Caching backend configured for this deployment.

    The function respects an explicit ``CACHE_TYPE`` override provided via
    :func:`os.getenv`. When the variable is unset and the Compose ``cache``
    profile is active (managed by :func:`_read_compose_profiles`), the helper
    defaults to ``redis`` so the application uses the bundled Redis service. In
    all other scenarios the function returns ``null`` to keep caching disabled.

    Returns:
        str: The cache backend identifier understood by
        :class:`flask_caching.Cache`.
    """

    configured = os.getenv("CACHE_TYPE")
    if configured:
        return configured

    if "cache" in _read_compose_profiles():
        return "redis"

    return "null"


def _resolve_cache_redis_url() -> Optional[str]:
    """Return the Redis connection URI used by Flask-Caching.

    Deployments can override ``CACHE_REDIS_URL`` via :func:`os.getenv`. When it
    is missing and the ``cache`` profile is active, the helper points Flask at
    ``redis://redis:6379/0`` to match the hostname and port declared in
    ``docker-compose.yml``. Otherwise ``None`` is returned so Flask-Caching can
    fall back to its in-memory store.

    Returns:
        Optional[str]: The Redis URI for Flask-Caching or ``None`` when Redis is
        not configured.
    """

    configured = os.getenv("CACHE_REDIS_URL")
    if configured:
        return configured

    if "cache" in _read_compose_profiles():
        return "redis://redis:6379/0"

    return None


def _resolve_ratelimit_storage_uri() -> str:
    """Determine where :mod:`flask_limiter` persists rate-limit counters.

    The function prioritises the ``RATELIMIT_STORAGE_URI`` environment variable
    retrieved via :func:`os.getenv`. When absent and the Compose ``cache``
    profile is enabled, a Redis URI targeting database ``1`` is returned so the
    limiter keeps counters separate from the application cache. Otherwise the
    function falls back to ``memory://`` which scopes counters to each Gunicorn
    worker.

    Returns:
        str: The storage URI consumed by :class:`flask_limiter.Limiter`.
    """

    configured = os.getenv("RATELIMIT_STORAGE_URI")
    if configured:
        return configured

    if "cache" in _read_compose_profiles():
        return "redis://redis:6379/1"

    return "memory://"


def _resolve_mail_allowed_sender_domain(default_sender: str) -> str:
    """Return the domain enforced for :data:`MAIL_DEFAULT_SENDER`.

    Args:
        default_sender: Email address configured as the default sender.

    Returns:
        str: Lowercase domain enforced by :func:`services.mail.validate_sender_domain`,
        defaulting to the domain portion of ``default_sender`` when no explicit
        override is provided.

    External Dependencies:
        Calls :func:`os.getenv` to honour the ``MAIL_ALLOWED_SENDER_DOMAIN``
        override supplied via environment variables.
    """

    override = os.getenv("MAIL_ALLOWED_SENDER_DOMAIN")
    if override is not None:
        return override.strip().lower()

    if "@" not in default_sender:
        return ""

    return default_sender.split("@", 1)[1].strip().lower()


def _resolve_oidc_scopes() -> Tuple[str, ...]:
    """Return OpenID Connect scopes requested during the authorization step."""

    raw_scopes = os.getenv("OIDC_SCOPES")
    if not raw_scopes:
        return ("openid", "email", "profile")

    scopes: List[str] = []
    for chunk in raw_scopes.split(","):
        for scope in chunk.split():
            candidate = scope.strip()
            if candidate:
                scopes.append(candidate)
    return tuple(scopes) if scopes else ("openid", "email", "profile")


def _resolve_oidc_audience() -> Tuple[str, ...]:
    """Return optional audiences validated on received ID tokens."""

    raw_audience = os.getenv("OIDC_AUDIENCE", "")
    values = [item.strip() for item in raw_audience.split(",") if item.strip()]
    return tuple(values)


def _resolve_oidc_allowed_domain() -> str:
    """Return the corporate email domain permitted for employee SSO access."""

    configured = os.getenv("OIDC_ALLOWED_DOMAIN", "freightservices.net").strip()
    if configured.startswith("@"):
        configured = configured[1:]
    return configured.lower() or "freightservices.net"


def _is_cloud_run_environment() -> bool:
    """Return ``True`` when running in a Cloud Run environment.

    Cloud Run sets ``K_SERVICE`` and ``K_REVISION`` environment variables for
    each deployment. The helper uses their presence to identify the platform
    and enable Cloud Run-specific defaults.

    Returns:
        bool: ``True`` when Cloud Run environment variables are detected.

    External Dependencies:
        Calls :func:`os.getenv` to read ``K_SERVICE`` and ``K_REVISION``.
    """

    return bool(os.getenv("K_SERVICE") or os.getenv("K_REVISION"))


def _resolve_branding_storage() -> str:
    """Return the branding storage backend, defaulting to GCS on Cloud Run.

    Returns:
        str: Normalized branding storage backend identifier.

    External Dependencies:
        Calls :func:`os.getenv` to read ``BRANDING_STORAGE`` and Cloud Run
        environment markers.
    """

    configured = os.getenv("BRANDING_STORAGE")
    if configured:
        return configured.strip().lower()

    if _is_cloud_run_environment():
        return "gcs"

    return "local"


class Config:
    SECRET_KEY = _resolve_secret_key()
    _sanitized_database_url = _sanitize_database_url(os.getenv("DATABASE_URL"))
    _cloud_sql_uri = build_cloud_sql_unix_socket_uri_from_env()
    _postgres_uri = build_postgres_database_uri_from_env()
    _compose_env_present = any(
        os.getenv(var)
        for var in (
            "POSTGRES_PASSWORD",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_OPTIONS",
        )
    )

    if _cloud_sql_uri:
        SQLALCHEMY_DATABASE_URI = _cloud_sql_uri
    elif _postgres_uri:
        SQLALCHEMY_DATABASE_URI = _postgres_uri
    elif _compose_env_present:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{DEFAULT_DB_PATH}"
    elif _sanitized_database_url:
        SQLALCHEMY_DATABASE_URI = _sanitized_database_url
    else:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{DEFAULT_DB_PATH}"
    if _is_production_environment() and not _is_postgres_dsn(SQLALCHEMY_DATABASE_URI):
        _record_startup_error(
            "Cloud Run deployments require an external PostgreSQL database. "
            "Configure DATABASE_URL with a Postgres DSN or set "
            "CLOUD_SQL_CONNECTION_NAME along with POSTGRES_USER, "
            "POSTGRES_PASSWORD, and POSTGRES_DB."
        )
    DATABASE_URL = _sanitized_database_url or ""
    POSTGRES_USER = os.getenv("POSTGRES_USER", "").strip()
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "").strip()
    POSTGRES_DB = os.getenv("POSTGRES_DB", "").strip()
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "").strip()
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "").strip()
    POSTGRES_OPTIONS = os.getenv("POSTGRES_OPTIONS", "").strip()
    CLOUD_SQL_CONNECTION_NAME = os.getenv("CLOUD_SQL_CONNECTION_NAME", "").strip()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SETUP_MODE = os.getenv("SETUP_MODE", "false").lower() in {
        "true",
        "1",
        "yes",
        "y",
    }
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
    DB_POOL_SIZE = _get_optional_int_from_env("DB_POOL_SIZE")
    DB_POOL_RECYCLE = _get_int_from_env("DB_POOL_RECYCLE", 1800)
    DB_POOL_MAX_OVERFLOW = _get_int_from_env("DB_POOL_MAX_OVERFLOW", 5)
    SQLALCHEMY_ENGINE_OPTIONS = _build_sqlalchemy_engine_options(
        pool_size=DB_POOL_SIZE,
        pool_recycle=DB_POOL_RECYCLE,
        max_overflow=DB_POOL_MAX_OVERFLOW,
    )
    CACHE_TYPE = _resolve_cache_type()
    CACHE_REDIS_URL = _resolve_cache_redis_url()
    # Mail/reset settings (optional):
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "quote@freightservices.net")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = _get_int_from_env("MAIL_PORT", 587)
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() in {
        "true",
        "1",
        "yes",
        "y",
    }
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() in {
        "true",
        "1",
        "yes",
        "y",
    }
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_ALLOWED_SENDER_DOMAIN = _resolve_mail_allowed_sender_domain(
        MAIL_DEFAULT_SENDER
    )
    MAIL_PRIVILEGED_DOMAIN = os.getenv("MAIL_PRIVILEGED_DOMAIN", "freightservices.net")
    MAIL_RATE_LIMIT_PER_USER_PER_HOUR = int(
        os.getenv("MAIL_RATE_LIMIT_PER_USER_PER_HOUR", 10)
    )
    MAIL_RATE_LIMIT_PER_USER_PER_DAY = int(
        os.getenv("MAIL_RATE_LIMIT_PER_USER_PER_DAY", 50)
    )
    MAIL_RATE_LIMIT_PER_FEATURE_PER_HOUR = int(
        os.getenv("MAIL_RATE_LIMIT_PER_FEATURE_PER_HOUR", 200)
    )
    MAIL_RATE_LIMIT_PER_RECIPIENT_PER_DAY = int(
        os.getenv("MAIL_RATE_LIMIT_PER_RECIPIENT_PER_DAY", 25)
    )
    WTF_CSRF_ENABLED = True
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per day;50 per hour")
    RATELIMIT_STORAGE_URI = _resolve_ratelimit_storage_uri()
    RATELIMIT_HEADERS_ENABLED = os.getenv(
        "RATELIMIT_HEADERS_ENABLED", "true"
    ).lower() in {
        "true",
        "1",
        "yes",
        "y",
    }
    AUTH_LOGIN_RATE_LIMIT = os.getenv("AUTH_LOGIN_RATE_LIMIT", "5 per minute")
    AUTH_REGISTER_RATE_LIMIT = os.getenv("AUTH_REGISTER_RATE_LIMIT", "5 per minute")
    AUTH_RESET_RATE_LIMIT = os.getenv("AUTH_RESET_RATE_LIMIT", "5 per minute")
    AUTH_RESET_TOKEN_RATE_LIMIT = os.getenv(
        "AUTH_RESET_TOKEN_RATE_LIMIT", "1 per 15 minutes"
    )
    API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN")
    API_QUOTE_RATE_LIMIT = os.getenv("API_QUOTE_RATE_LIMIT", "30 per minute")
    _cloud_run = _is_cloud_run_environment()
    BRANDING_STORAGE = _resolve_branding_storage()
    GCS_BUCKET = os.getenv("GCS_BUCKET")
    GCS_PREFIX = os.getenv("GCS_PREFIX")
    OIDC_ISSUER = os.getenv("OIDC_ISSUER")
    OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET")
    OIDC_REDIRECT_URI = os.getenv("OIDC_REDIRECT_URI")
    OIDC_SCOPES = _resolve_oidc_scopes()
    OIDC_AUDIENCE = _resolve_oidc_audience()
    OIDC_ALLOWED_DOMAIN = _resolve_oidc_allowed_domain()
    OIDC_END_SESSION_ENDPOINT = os.getenv("OIDC_END_SESSION_ENDPOINT")

    if _cloud_run:
        if BRANDING_STORAGE not in {
            "gcs",
            "google",
            "google_cloud_storage",
            "googlecloudstorage",
        }:
            _record_startup_error(
                "Cloud Run requires BRANDING_STORAGE=gcs for branding assets."
            )
        if not (GCS_BUCKET or "").strip():
            _record_startup_error(
                "GCS_BUCKET must be set for branding storage on Cloud Run."
            )

    CONFIG_ERRORS = list(_CONFIG_ERRORS)
