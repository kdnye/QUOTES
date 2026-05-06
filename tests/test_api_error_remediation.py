from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

import app as app_module
from app import create_app
from app.models import User, db


class ApiErrorRemediationConfig:
    """Configuration overrides for JSON API error remediation tests.

    Inputs:
        None. Values are provided through class attributes.

    Outputs:
        Flask configuration consumed by :func:`app.create_app`.

    External dependencies:
        Consumed by :func:`app.create_app` during Flask initialization.
    """

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False
    API_AUTH_TOKEN = "expected-token"


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    """Create an application configured for API remediation error tests.

    Args:
        monkeypatch: Fixture used to control startup environment variables.

    Returns:
        Iterator[Flask]: Yields a Flask application test instance.

    External dependencies:
        * Calls :func:`monkeypatch.setenv` to disable startup migrations.
        * Calls :func:`app.create_app` to construct the application.
        * Calls :func:`app.models.db.create_all` and writes a test
          :class:`app.models.User` to satisfy setup checks.
    """

    monkeypatch.setenv("MIGRATE_ON_STARTUP", "false")
    monkeypatch.setattr(app_module, "_is_setup_required", lambda: False)
    test_app = create_app(ApiErrorRemediationConfig)
    with test_app.app_context():
        db.create_all()
        user = User(email="api-test-user@example.com", role="super_admin")
        user.set_password("StrongPassw0rd!")
        db.session.add(user)
        db.session.commit()

    yield test_app


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    """Return a Flask test client for the remediation API app.

    Args:
        app: Flask application configured for this test module.

    Returns:
        FlaskClient: Test client bound to ``app``.

    External dependencies:
        Calls :meth:`flask.Flask.test_client`.
    """

    return app.test_client()


def test_missing_authorization_header_returns_remediation(client: FlaskClient) -> None:
    """Return an actionable remediation when Authorization is omitted.

    Args:
        client: Flask API test client.

    Returns:
        None. Assertions validate response status and payload.

    External dependencies:
        Calls :meth:`flask.testing.FlaskClient.post` for API execution.
    """

    response = client.post("/api/quote", json={"quote_type": "Hotshot"})

    assert response.status_code == 401
    assert response.get_json() == {
        "error": "Missing Authorization header.",
        "remediation": (
            "Provide an Authorization header using 'Bearer <your_api_key>' "
            "and retry the request."
        ),
    }


def test_invalid_quote_type_returns_remediation(client: FlaskClient) -> None:
    """Return an actionable remediation when quote_type is unsupported.

    Args:
        client: Flask API test client.

    Returns:
        None. Assertions validate response status and payload.

    External dependencies:
        Calls :meth:`flask.testing.FlaskClient.post` for API execution.
    """

    response = client.post(
        "/api/quote",
        headers={"Authorization": "Bearer expected-token"},
        json={"quote_type": "Ocean"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Invalid quote_type",
        "remediation": "Set quote_type to either 'Hotshot' or 'Air' and retry.",
    }


def test_quote_not_found_returns_remediation(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return an actionable remediation when a quote lookup misses.

    Args:
        client: Flask API test client.
        monkeypatch: Fixture used to replace data access during tests.

    Returns:
        None. Assertions validate response status and payload.

    External dependencies:
        * Calls :func:`monkeypatch.setattr` to replace
          :func:`app.services.quote.get_quote`.
        * Calls :meth:`flask.testing.FlaskClient.get` for API execution.
    """

    monkeypatch.setattr("app.api.quote_service.get_quote", lambda _quote_id: None)

    response = client.get(
        "/api/quote/Q-UNKNOWN1",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "Quote not found",
        "remediation": (
            "Confirm the quote_id exists and belongs to this environment, then "
            "retry the lookup."
        ),
    }
