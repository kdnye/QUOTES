from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest


@pytest.fixture(scope="session")
def postgres_database_url() -> str:
    """Return the PostgreSQL test database URL from the environment.

    Args:
        None.

    Returns:
        str: PostgreSQL SQLAlchemy database URL for test runs.

    External Dependencies:
        * Reads ``TEST_DATABASE_URL`` via :func:`os.getenv`.
        * Skips tests via :func:`pytest.skip` when the environment is missing.
    """

    raw_url = os.getenv("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "TEST_DATABASE_URL is not set; skipping PostgreSQL-dependent tests."
        )

    parsed = urlparse(raw_url)
    if not parsed.scheme.startswith("postgres"):
        raise ValueError("TEST_DATABASE_URL must be a PostgreSQL DSN.")

    return raw_url
