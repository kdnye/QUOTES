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


class _FakeUser:
    """Simple user stub used to control ``current_user`` in route tests.

    Inputs:
        is_authenticated: Whether the test user should be treated as logged in.
        rate_set: Optional assigned rate set for logo lookup.

    Outputs:
        Instance attributes accessed by ``/customer-logo``.

    External dependencies:
        * Mimics the interface expected from :mod:`flask_login.current_user`.
    """

    def __init__(self, is_authenticated: bool, rate_set: str | None = None) -> None:
        self.is_authenticated = is_authenticated
        self.rate_set = rate_set


class _FakeLogoMapping:
    """Minimal mapping object that mirrors ``RateSetLogo`` rows for tests."""

    def __init__(self, filename: str) -> None:
        self.filename = filename


class _FakeLogoQuery:
    """Query helper that returns deterministic logo mappings in tests.

    Inputs:
        mappings: Dictionary of rate-set-to-filename values.

    Outputs:
        ``filter_by(...).first()`` results matching SQLAlchemy's interface.

    External dependencies:
        * Used by ``_FakeRateSetLogoModel`` to replace
          :attr:`app.RateSetLogo.query`.
    """

    def __init__(self, mappings: dict[str, str]) -> None:
        self._mappings = mappings

    def filter_by(self, **kwargs: str):
        """Store filter kwargs and return ``self`` for query chaining."""

        self._rate_set = kwargs.get("rate_set")
        return self

    def first(self) -> _FakeLogoMapping | None:
        """Return a mapping object for the previously requested rate set."""

        if self._rate_set in self._mappings:
            return _FakeLogoMapping(self._mappings[self._rate_set])
        return None


class _FakeRateSetLogoModel:
    """Model stub exposing a ``query`` attribute compatible with route logic."""

    def __init__(self, mappings: dict[str, str]) -> None:
        self.query = _FakeLogoQuery(mappings)


def test_get_customer_logo_returns_no_content_for_unauthenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return 204 when no authenticated user is available for logo lookup.

    Inputs:
        monkeypatch: Pytest monkeypatch helper for replacing runtime globals.

    Outputs:
        None. Assertions validate the HTTP status code.

    External dependencies:
        * Replaces :data:`app.current_user` with ``_FakeUser``.
    """

    app = _build_test_app(monkeypatch)
    monkeypatch.setattr("app.current_user", _FakeUser(False), raising=False)

    client = app.test_client()
    response = client.get("/customer-logo")

    assert response.status_code == 204


def test_get_customer_logo_serves_configured_customer_logo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve the mapped customer logo file when user and mapping are valid.

    Inputs:
        tmp_path: Temporary path where a fake logo file is written.
        monkeypatch: Pytest monkeypatch helper for replacing module globals.

    Outputs:
        None. Assertions validate successful image delivery.

    External dependencies:
        * Replaces :data:`app.current_user` and :data:`app.RateSetLogo`.
        * Uses the ``CUSTOMER_LOGOS_DIR`` environment variable.
    """

    app = _build_test_app(monkeypatch)
    logo_bytes = b"\x89PNG\r\n\x1a\ncustomer"
    logo_file = tmp_path / "AGR Logo.png"
    logo_file.write_bytes(logo_bytes)

    monkeypatch.setenv("CUSTOMER_LOGOS_DIR", str(tmp_path))
    monkeypatch.setattr("app.current_user", _FakeUser(True, "agr"), raising=False)
    monkeypatch.setattr(
        "app.RateSetLogo",
        _FakeRateSetLogoModel({"agr": "AGR Logo.png"}),
        raising=False,
    )

    client = app.test_client()
    response = client.get("/customer-logo")

    assert response.status_code == 200
    assert response.get_data() == logo_bytes
