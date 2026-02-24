from __future__ import annotations

from pathlib import Path

from flask import Flask
import pytest

from app import create_app


class TestLogoRouteConfig:
    """Configuration overrides for logo route tests.

    Inputs:
        None. Uses class attributes consumed by :func:`app.create_app`.

    Outputs:
        Provides minimal runtime settings for Flask route tests.

    External dependencies:
        * Consumed by :func:`app.create_app` while initializing extensions.
    """

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = (
        "postgresql+psycopg2://user:pass@localhost:5432/quote_tool"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False


def _build_test_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create an application instance for logo route testing.

    Inputs:
        None.

    Outputs:
        Flask app configured with lightweight startup options.

    External dependencies:
        * Calls :func:`pytest.MonkeyPatch.setattr` to bypass database-backed
          setup checks in :mod:`app.__init__`.
        * Calls :func:`app.create_app` to construct the Flask app.
    """

    monkeypatch.setattr("app._is_setup_required", lambda: False)
    return create_app(TestLogoRouteConfig)


def test_get_logo_returns_image_when_configured_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve the configured logo file when it exists on disk.

    Inputs:
        tmp_path: Temporary filesystem directory managed by pytest.

    Outputs:
        None. Assertions validate the HTTP status and binary response body.

    External dependencies:
        * Uses :class:`pathlib.Path` to create a temporary PNG fixture.
        * Exercises ``/fsi-logo`` through Flask's test client.
    """

    app = _build_test_app(monkeypatch)
    logo_path = tmp_path / "fsi-logo.png"
    expected_bytes = b"\x89PNG\r\n\x1a\nfixture"
    logo_path.write_bytes(expected_bytes)
    app.config["FSI_LOGO_PATH"] = str(logo_path)

    client = app.test_client()
    response = client.get("/fsi-logo")

    assert response.status_code == 200
    assert response.get_data() == expected_bytes


def test_get_logo_returns_not_found_when_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return HTTP 404 when the configured logo file is missing.

    Inputs:
        tmp_path: Temporary filesystem directory managed by pytest.

    Outputs:
        None. Assertions validate the missing-file response.

    External dependencies:
        * Exercises ``/fsi-logo`` through Flask's test client.
    """

    app = _build_test_app(monkeypatch)
    missing_path = tmp_path / "missing-logo.png"
    app.config["FSI_LOGO_PATH"] = str(missing_path)

    client = app.test_client()
    response = client.get("/fsi-logo")

    assert response.status_code == 404
