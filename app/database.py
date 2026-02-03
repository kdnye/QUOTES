"""Database engine and session configuration utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import scoped_session, sessionmaker

from config import Config
from app.models import (
    db,
    User,
    Quote,
    EmailQuoteRequest,
    EmailDispatchLog,
    Accessorial,
    HotshotRate,
    BeyondRate,
    AirCostZone,
    ZipZone,
    CostZone,
    RateUpload,
)


engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS
)
# Use scoped_session to provide thread-local sessions that are removed
# after each request context. Sessions should be acquired with ``Session()``
# and cleaned up via ``Session.remove()``.
Session = scoped_session(sessionmaker(bind=engine))

# For legacy compatibility, expose Base for metadata operations in tests
Base = db.Model


def ensure_database_schema(active_engine: Optional[Engine] = None) -> None:
    """Provision required tables for the configured database backend.

    The helper always invokes :func:`_run_alembic_upgrade` to ensure Alembic
    migrations reach the latest revision automatically.

    Args:
        active_engine: Optional SQLAlchemy engine. When ``None`` the module's
            global :data:`engine` is used so the function can be invoked from
            application startup as well as tests.

    Returns:
        None. The function performs schema creation as a side effect.

    External Dependencies:
        Calls :func:`app.database._run_alembic_upgrade` to apply migrations.
    """

    selected_engine = active_engine or engine
    _run_alembic_upgrade(selected_engine)


def _escape_alembic_url(rendered_url: str) -> str:
    """Prepare database URLs for Alembic's ConfigParser interpolation rules.

    Args:
        rendered_url: Fully rendered SQLAlchemy database URL.

    Returns:
        str: URL with literal percent signs escaped as ``%%`` so the Alembic
        configuration parser treats URL-encoded values literally.

    External Dependencies:
        None. This helper performs string manipulation only.
    """

    return rendered_url.replace("%", "%%")


def _run_alembic_upgrade(active_engine: Engine) -> None:
    """Apply Alembic migrations for the database bound to ``active_engine``.

    Args:
        active_engine: SQLAlchemy engine configured for the target database.

    Returns:
        ``None``. Executes ``alembic upgrade head`` programmatically so the
        runtime schema always matches the latest migrations. Alembic reads the
        migration scripts from ``migrations/`` and connects using the engine
        URL extracted from ``active_engine``. The helper renders the URL with
        passwords intact by calling :meth:`sqlalchemy.engine.URL.render_as_string`
        when available to avoid relying on environment variables inside the
        container. The Alembic configuration resolves paths relative to the
        repository root derived from :data:`__file__`, making the upgrade
        process independent of the current working directory or Docker-specific
        volume mounts.

    External Dependencies:
        Calls :func:`app.database._escape_alembic_url` to sanitize the URL for
        Alembic's :class:`configparser.ConfigParser`.
    """

    project_root = Path(__file__).resolve().parent.parent
    alembic_config_path = project_root / "alembic.ini"
    migrations_path = project_root / "migrations"
    if alembic_config_path.exists():
        config = AlembicConfig(str(alembic_config_path))
    else:
        config = AlembicConfig()

    config.set_main_option("script_location", str(migrations_path))

    url = active_engine.url
    if hasattr(url, "render_as_string"):
        rendered_url = url.render_as_string(hide_password=False)
    else:  # pragma: no cover - compatibility fallback for older SQLAlchemy
        rendered_url = str(url)

    # Escape '%' so ConfigParser treats URL-encoded values literally.
    config.set_main_option("sqlalchemy.url", _escape_alembic_url(rendered_url))

    inspector = inspect(active_engine)
    existing_tables = [table for table in inspector.get_table_names() if table]
    has_version_table = "alembic_version" in existing_tables

    if not has_version_table and existing_tables:
        script = ScriptDirectory.from_config(config)
        base_revision = script.get_base()
        if isinstance(base_revision, tuple):  # pragma: no cover - multi-base fallback
            base_revision = base_revision[0]
        stamp_revision = base_revision or "base"
        command.stamp(config, stamp_revision)

    command.upgrade(config, "head")


__all__ = [
    "engine",
    "Session",
    "Base",
    "User",
    "Quote",
    "EmailQuoteRequest",
    "EmailDispatchLog",
    "Accessorial",
    "HotshotRate",
    "BeyondRate",
    "AirCostZone",
    "ZipZone",
    "CostZone",
    "RateUpload",
    "ensure_database_schema",
]


if __name__ == "__main__":
    ensure_database_schema()
