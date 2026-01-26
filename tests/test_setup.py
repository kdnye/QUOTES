from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app
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
            "database_url": "postgresql+psycopg2://user:pass@db/quote_tool",
            "postgres_user": "db-user",
            "postgres_password": "db-pass",
            "postgres_db": "db-name",
            "postgres_host": "db.example.com",
            "postgres_port": "5432",
            "postgres_options": "sslmode=require",
            "cloud_sql_connection_name": "project:region:instance",
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
        assert (
            settings["database_url"].value
            == "postgresql+psycopg2://user:pass@db/quote_tool"
        )
        assert settings["database_url"].is_secret is True
        assert settings["postgres_user"].value == "db-user"
        assert settings["postgres_user"].is_secret is False
        assert settings["postgres_password"].value == "db-pass"
        assert settings["postgres_password"].is_secret is True
        assert settings["postgres_db"].value == "db-name"
        assert settings["postgres_db"].is_secret is False
        assert settings["postgres_host"].value == "db.example.com"
        assert settings["postgres_host"].is_secret is False
        assert settings["postgres_port"].value == "5432"
        assert settings["postgres_port"].is_secret is False
        assert settings["postgres_options"].value == "sslmode=require"
        assert settings["postgres_options"].is_secret is False
        assert settings["cloud_sql_connection_name"].value == "project:region:instance"
        assert settings["cloud_sql_connection_name"].is_secret is False
        assert app.config["GOOGLE_MAPS_API_KEY"] == "maps-key"
        assert app.config["GCS_BUCKET"] == "branding-bucket"
        assert (
            app.config["DATABASE_URL"]
            == "postgresql+psycopg2://user:pass@db/quote_tool"
        )
        assert app.config["POSTGRES_USER"] == "db-user"
        assert app.config["POSTGRES_PASSWORD"] == "db-pass"
        assert app.config["POSTGRES_DB"] == "db-name"
        assert app.config["POSTGRES_HOST"] == "db.example.com"
        assert app.config["POSTGRES_PORT"] == 5432
        assert app.config["POSTGRES_OPTIONS"] == "sslmode=require"
        assert app.config["CLOUD_SQL_CONNECTION_NAME"] == "project:region:instance"


def test_setup_updates_cloud_run_for_infra_overrides(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Send infra overrides to Cloud Run when running on Cloud Run."""

    recorded: dict[str, dict[str, str]] = {}

    def fake_update_cloud_run_service(env_vars_map: dict[str, str]) -> None:
        """Capture infra updates for assertions."""

        recorded["env_vars_map"] = env_vars_map

    monkeypatch.setenv("K_SERVICE", "quote-service")
    monkeypatch.setattr(
        "app.setup.update_cloud_run_service",
        fake_update_cloud_run_service,
    )

    response = client.post(
        "/setup",
        data={
            "google_maps_api_key": "maps-key",
            "gcs_bucket": "branding-bucket",
            "database_url": "postgresql+psycopg2://user:pass@db/quote_tool",
            "postgres_user": "db-user",
            "postgres_password": "db-pass",
            "postgres_db": "db-name",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert b"Applying Cloud Run updates" in response.data
    assert recorded["env_vars_map"] == {
        "DATABASE_URL": "postgresql+psycopg2://user:pass@db/quote_tool",
        "POSTGRES_USER": "db-user",
        "POSTGRES_PASSWORD": "db-pass",
        "POSTGRES_DB": "db-name",
    }

    with app.app_context():
        from app.models import AppSetting

        settings = {setting.key: setting for setting in AppSetting.query.all()}
        assert settings["google_maps_api_key"].value == "maps-key"
        assert settings["gcs_bucket"].value == "branding-bucket"
        assert "database_url" not in settings
        assert "postgres_user" not in settings
        assert "postgres_password" not in settings
        assert "postgres_db" not in settings
