"""Pytest configuration to ensure config imports have a valid database URL."""

import os


os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
)
