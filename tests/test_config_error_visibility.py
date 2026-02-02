from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app


def _build_error_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment: str,
    show_flag: Optional[str],
) -> Flask:
    """Create an application configured with startup configuration errors.

    Args:
        tmp_path: Temporary path injected by pytest for SQLite storage.
        monkeypatch: Pytest fixture used to manage environment overrides.
        environment: Environment name used to emulate production or development.
        show_flag: Optional ``SHOW_CONFIG_ERRORS`` flag value.

    Returns:
        A Flask application configured with a startup configuration error.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
        * Uses :func:`monkeypatch.setenv` and :func:`monkeypatch.delenv` to
          manage environment variables.
        * Relies on :mod:`flask_sqlalchemy` to initialize a SQLite database.
    """

    monkeypatch.setenv("ENVIRONMENT", environment)
    if show_flag is None:
        monkeypatch.delenv("SHOW_CONFIG_ERRORS", raising=False)
    else:
        monkeypatch.setenv("SHOW_CONFIG_ERRORS", show_flag)

    class ErrorConfig:
        """Configuration overrides for config error visibility tests.

        Inputs:
            None. Uses class attributes for configuration.

        Outputs:
            Defines settings consumed by :func:`app.create_app`.

        External dependencies:
            * Consumed by :func:`app.create_app` to configure SQLAlchemy.
        """

        TESTING = True
        SECRET_KEY = "test-secret-key"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        STARTUP_DB_CHECKS = True
        CONFIG_ERRORS = ["Missing POSTGRES_PASSWORD"]

    return create_app(ErrorConfig)


@pytest.mark.parametrize(
    ("environment", "show_flag", "expect_visible"),
    [
        ("production", None, False),
        ("production", "true", True),
        ("development", None, True),
    ],
)
def test_config_error_visibility_respects_environment_and_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    show_flag: Optional[str],
    expect_visible: bool,
) -> None:
    """Ensure config error diagnostics obey environment and opt-in flags.

    Args:
        tmp_path: Temporary path injected by pytest for SQLite storage.
        monkeypatch: Pytest fixture used to manage environment overrides.
        environment: Environment name used to emulate production or development.
        show_flag: Optional ``SHOW_CONFIG_ERRORS`` flag value.
        expect_visible: Expected flag for diagnostics visibility.

    Returns:
        None. Assertions validate the response behavior.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
        * Exercises Flask routes via :class:`flask.testing.FlaskClient`.
    """

    app = _build_error_app(
        tmp_path,
        monkeypatch,
        environment=environment,
        show_flag=show_flag,
    )
    client: FlaskClient = app.test_client()

    response = client.get("/healthz/config")
    if expect_visible:
        assert response.status_code == 200
        assert response.get_json() == {"errors": ["Missing POSTGRES_PASSWORD"]}
    else:
        assert response.status_code == 404

    if environment == "production":
        page = client.get("/")
        assert page.status_code == 500
        body = page.get_data(as_text=True)
        if expect_visible:
            assert "Missing POSTGRES_PASSWORD" in body
        else:
            assert "Missing POSTGRES_PASSWORD" not in body
