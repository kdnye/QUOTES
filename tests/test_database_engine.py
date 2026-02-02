from __future__ import annotations

import importlib
import sys
from pathlib import Path

import sqlalchemy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

import config


def test_create_engine_receives_configured_options(monkeypatch) -> None:
    """Ensure SQLAlchemy engine options are passed into ``create_engine``.

    Args:
        monkeypatch: Pytest fixture for safely patching attributes.

    Returns:
        None. Asserts the mocked ``create_engine`` receives the configured
        options before the module reload completes.
    """

    captured: dict[str, object] = {}
    real_create_engine = sqlalchemy.create_engine

    def fake_create_engine(url: str, **kwargs: object) -> object:
        """Capture ``create_engine`` inputs for assertion.

        Args:
            url: SQLAlchemy database URL under test.
            **kwargs: Keyword arguments forwarded to SQLAlchemy.

        Returns:
            object: Placeholder engine instance for module import.
        """

        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    monkeypatch.setattr(
        config.Config,
        "SQLALCHEMY_DATABASE_URI",
        "postgresql+psycopg2://user:pass@localhost:5432/test_db",
    )
    expected_options = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "max_overflow": 5,
        "pool_size": 3,
    }
    monkeypatch.setattr(config.Config, "SQLALCHEMY_ENGINE_OPTIONS", expected_options)

    import app.database as database

    importlib.reload(database)

    assert captured["url"] == "postgresql+psycopg2://user:pass@localhost:5432/test_db"
    assert captured["kwargs"] == expected_options

    monkeypatch.setattr(sqlalchemy, "create_engine", real_create_engine)
    importlib.reload(database)
