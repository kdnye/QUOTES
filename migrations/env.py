from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
import sys

from flask import current_app
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.database import Base, db  # isort: skip  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Import necessary functions from config.py
    from config import (
        build_cloud_sql_unix_socket_uri_from_env,
        build_postgres_database_uri_from_env,
        _rebuild_database_url
    )
    import os

    # Determine the database URI from environment variables
    _cloud_sql_uri = build_cloud_sql_unix_socket_uri_from_env()
    _postgres_uri = build_postgres_database_uri_from_env()
    _raw_database_url = os.getenv("DATABASE_URL")
    _sanitized_database_url = _rebuild_database_url(_raw_database_url)

    if _cloud_sql_uri:
        db_url = _cloud_sql_uri
    elif _postgres_uri:
        db_url = _postgres_uri
    elif _sanitized_database_url:
        db_url = _sanitized_database_url
    else:
        db_url = config.get_main_option("sqlalchemy.url")

    if db_url:
        # ConfigParser treats `%` as interpolation, so `%` must be escaped.
        config.set_main_option("sqlalchemy.url", str(db_url).replace("%", "%%"))

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
