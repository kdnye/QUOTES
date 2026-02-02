"""Backfill the relational quotes table with identifiers and weight metadata.

The script reads the database location from :class:`config.Config`, defaulting
to the host provided by ``POSTGRES_HOST`` (``"postgres"`` in Docker Compose)
when ``POSTGRES_PASSWORD`` is defined. It uses :func:`sqlalchemy.create_engine`
so the migration works with PostgreSQL deployments. ``app.models.QUOTES_TABLE``
is ensured to contain the ``quote_id`` and ``weight_method`` columns, and
existing rows receive generated UUIDs via :func:`uuid.uuid4` with a default
``"actual"`` weight method. The script is idempotent and may be run multiple
times without creating duplicate data.
"""

from __future__ import annotations

import os
import uuid
from typing import Dict, List
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

load_dotenv()

from config import (
    Config,
    build_postgres_database_uri_from_env,
)  # noqa: E402  # Loaded after dotenv for environment values
from app.models import QUOTES_TABLE


def _resolve_database_url() -> str:
    """Return the connection string used to run the migration.

    The helper inspects ``DATABASE_URL`` first so custom deployments are
    respected. When ``DATABASE_URL`` is unset it delegates to
    :func:`config.build_postgres_database_uri_from_env` so maintenance scripts
    share Docker Compose's defaults and honour overrides like
    ``POSTGRES_HOST``. Otherwise the function falls back to
    :class:`config.Config` which provides the configured PostgreSQL default.

    Returns:
        str: SQLAlchemy-compatible connection string.

    Raises:
        ValueError: If the resolved database URL is not a PostgreSQL DSN.

    External Dependencies:
        * Calls :func:`os.getenv` to read ``DATABASE_URL``.
        * Calls :func:`config.build_postgres_database_uri_from_env` to build
          a Compose-style PostgreSQL DSN.
        * Reads :data:`config.Config.SQLALCHEMY_DATABASE_URI` for the fallback.
        * Uses :func:`urllib.parse.urlparse` to validate the DSN scheme.
    """

    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        candidate_url = configured_url
    else:
        compose_url = build_postgres_database_uri_from_env()
        if compose_url:
            candidate_url = compose_url
        else:
            candidate_url = Config.SQLALCHEMY_DATABASE_URI

    parsed = urlparse(candidate_url)
    if not parsed.scheme.startswith("postgres"):
        raise ValueError(
            "PostgreSQL database configuration is required for quote migration."
        )

    return candidate_url


def column_exists(engine: Engine, table: str, column: str) -> bool:
    """Return ``True`` when a named column is present on a relational table.

    Args:
        engine: Database engine created via :func:`sqlalchemy.create_engine`.
        table: Name of the table being inspected.
        column: Column to search for within ``table``.

    Returns:
        bool: ``True`` when ``column`` is present on ``table``; otherwise
        ``False``.

    Side Effects:
        Uses :func:`sqlalchemy.inspect` to read schema metadata from the
        connected database.
    """

    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns(table)}
    return column in columns


def run_migration() -> None:
    """Synchronize the quotes table schema and data with application needs.

    The function opens an engine using :func:`sqlalchemy.create_engine` and the
    URL returned by :func:`_resolve_database_url`. It ensures
    :data:`app.models.QUOTES_TABLE` exposes ``quote_id`` and ``weight_method``
    columns, creating them with ``ALTER TABLE`` when necessary. Existing rows
    missing identifiers or weight metadata are updated in-place. All work
    occurs within a single transaction managed by :meth:`sqlalchemy.engine.Engine.begin`.
    """

    database_url = _resolve_database_url()
    engine = create_engine(database_url)

    with engine.begin() as connection:
        if not column_exists(engine, QUOTES_TABLE, "quote_id"):
            connection.execute(
                text(f"ALTER TABLE {QUOTES_TABLE} ADD COLUMN quote_id TEXT")
            )
            print("✅ Added 'quote_id' column.")

        if not column_exists(engine, QUOTES_TABLE, "weight_method"):
            connection.execute(
                text(f"ALTER TABLE {QUOTES_TABLE} ADD COLUMN weight_method TEXT")
            )
            print("✅ Added 'weight_method' column.")

        rows = connection.execute(
            text(f"SELECT id, quote_id, weight_method FROM {QUOTES_TABLE}")
        ).mappings()

        quote_id_updates: List[Dict[str, object]] = []
        weight_updates: List[Dict[str, object]] = []

        for row in rows:
            quote_id = row["quote_id"]
            weight_method = row["weight_method"]
            row_id = row["id"]

            if not quote_id or str(quote_id).strip() == "":
                quote_id_updates.append(
                    {
                        "quote_id": str(uuid.uuid4()),
                        "id": row_id,
                    }
                )

            if not weight_method:
                weight_updates.append({"method": "actual", "id": row_id})

        if weight_updates:
            connection.execute(
                text(
                    f"UPDATE {QUOTES_TABLE} SET weight_method = :method WHERE id = :id"
                ),
                weight_updates,
            )

        if quote_id_updates:
            connection.execute(
                text(f"UPDATE {QUOTES_TABLE} SET quote_id = :quote_id WHERE id = :id"),
                quote_id_updates,
            )

    engine.dispose()
    print(f"✅ Migrated {len(quote_id_updates)} quotes with new UUIDs.")
    print("✅ Migration complete.")


if __name__ == "__main__":
    run_migration()
