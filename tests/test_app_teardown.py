from __future__ import annotations

from unittest.mock import Mock

import pytest

from app import create_app


class TeardownConfig:
    """Configuration for teardown session registration tests.

    Inputs:
        None. Uses class attributes consumed by :func:`app.create_app`.

    Outputs:
        Provides Flask and SQLAlchemy settings for an in-memory test app.

    External dependencies:
        * Used by :func:`app.create_app` during Flask app initialization.
    """

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False


def test_create_app_registers_teardown_session_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure app teardown removes thread-local sessions.

    Inputs:
        monkeypatch: Fixture that patches :mod:`app.database` state.

    Outputs:
        None. Assertions verify session cleanup behavior.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
        * Patches :data:`app.database.Session.remove` to verify cleanup calls.
    """

    import app.database as database

    remove_mock = Mock()
    monkeypatch.setattr(database.Session, "remove", remove_mock)

    flask_app = create_app(TeardownConfig)

    with flask_app.app_context():
        assert remove_mock.call_count == 0

    remove_mock.assert_called_once_with()
