from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import _is_setup_required, create_app
from app.models import User, db


class TestConfig:
    """Configuration overrides for setup flow tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(tmp_path: Path) -> Flask:
    """Create a Flask app wired to a temporary SQLite database.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        A configured Flask application for tests.
    """

    TestConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(TestConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    """Return a test client for the configured Flask app.

    Args:
        app: Flask application fixture.

    Returns:
        Flask test client bound to ``app``.
    """

    return app.test_client()


def test_setup_redirects_when_no_users(client: FlaskClient) -> None:
    """Ensure setup redirects are enforced until a user exists."""

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/setup" in response.headers.get("Location", "")

    healthz = client.get("/healthz")
    assert healthz.status_code == 200

    setup_page = client.get("/setup")
    assert setup_page.status_code == 200


def test_setup_check_handles_database_error(
    app: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ensure setup checks default to False when the database is unavailable."""

    class DummyQuery:
        """Stub query object that simulates a database outage."""

        def count(self) -> int:
            """Raise a SQLAlchemy error to simulate a failed count."""

            raise SQLAlchemyError("db down")

    monkeypatch.setattr(User, "query", DummyQuery(), raising=False)

    with app.app_context(), caplog.at_level("WARNING"):
        result = _is_setup_required()

    assert result is False
    assert "Setup check skipped: database unavailable." in caplog.text


def test_setup_admin_creates_super_admin(app: Flask, client: FlaskClient) -> None:
    """Validate the setup admin form provisions a super admin user."""

    response = client.post(
        "/setup/admin",
        data={
            "email": "admin@example.com",
            "password": "StrongPassw0rd!",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/setup/complete" in response.headers.get("Location", "")

    with app.app_context():
        user = User.query.one()
        assert user.email == "admin@example.com"
        assert user.role == "super_admin"
        assert user.employee_approved is True
        assert user.check_password("StrongPassw0rd!")

    index_response = client.get("/", follow_redirects=False)
    assert index_response.status_code == 200


def test_setup_allows_config_overrides(app: Flask, client: FlaskClient) -> None:
    """Ensure setup form persists configuration overrides."""

    response = client.post(
        "/setup",
        data={
            "google_maps_api_key": "maps-key",
            "gcs_bucket": "branding-bucket",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        from app.models import AppSetting

        settings = {setting.key: setting for setting in AppSetting.query.all()}
        assert settings["google_maps_api_key"].value == "maps-key"
        assert settings["google_maps_api_key"].is_secret is True
        assert settings["gcs_bucket"].value == "branding-bucket"
        assert settings["gcs_bucket"].is_secret is False
        assert app.config["GOOGLE_MAPS_API_KEY"] == "maps-key"
        assert app.config["GCS_BUCKET"] == "branding-bucket"
