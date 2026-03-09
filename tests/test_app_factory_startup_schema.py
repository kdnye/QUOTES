from __future__ import annotations

from types import SimpleNamespace

import pytest

import app as app_module
from app import create_app


class StartupSchemaConfig:
    """Configuration used to validate app factory startup schema behavior."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False


def test_create_app_creates_tables_and_stamps_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the application factory provisions tables and stamps Alembic head.

    Args:
        monkeypatch: Fixture used to replace startup dependencies.

    Returns:
        None. Assertions validate startup calls.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
        * Replaces :func:`flask_migrate.stamp` through ``sys.modules`` to
          observe Alembic stamping behavior without touching a real migration
          environment.
        * Replaces :func:`app._is_setup_required` and
          :func:`app._verify_app_setup` to keep this test focused on the
          startup schema bootstrapping path.
    """

    calls = []

    def fake_create_all() -> None:
        """Record table creation requests from ``app.models.db.create_all``."""

        calls.append("create_all")

    def fake_stamp(revision: str = "head") -> None:
        """Record Alembic stamp revisions from ``flask_migrate.stamp``."""

        calls.append(("stamp", revision))

    monkeypatch.setattr(app_module.db, "create_all", fake_create_all)
    monkeypatch.setitem(
        __import__("sys").modules,
        "flask_migrate",
        SimpleNamespace(stamp=fake_stamp),
    )
    monkeypatch.setattr(app_module, "_verify_app_setup", lambda _: [])
    monkeypatch.setattr(app_module, "_is_setup_required", lambda: False)

    flask_app = create_app(StartupSchemaConfig)

    assert flask_app is not None
    assert "create_all" in calls
    assert ("stamp", "head") in calls
