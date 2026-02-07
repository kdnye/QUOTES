from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.exc import OperationalError

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
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create a Flask app wired to a PostgreSQL test database.

    Args:
        postgres_database_url: PostgreSQL connection string for tests.
        monkeypatch: Pytest fixture for environment overrides.

    Returns:
        A configured Flask application for tests.

    External dependencies:
        * Sets ``MIGRATE_ON_STARTUP`` via :func:`monkeypatch.setenv`.
        * Calls :func:`app.create_app` to build the Flask application.
    """

    TestConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
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


def test_setup_redirects_when_only_config_errors(
    monkeypatch: pytest.MonkeyPatch, postgres_database_url: str
) -> None:
    """Ensure config-only failures route to the setup checklist.

    Args:
        monkeypatch: Pytest fixture used to override environment settings.
        postgres_database_url: PostgreSQL connection string for tests.

    External dependencies:
        * Initializes :data:`app.models.db` with a temporary
          :class:`flask.Flask` app via :meth:`flask_sqlalchemy.SQLAlchemy.init_app`.
        * Calls :func:`app.create_app` to build the Flask application.
    """

    class ConfigWithErrors(TestConfig):
        """Configuration overrides for config-only setup failures."""

        SQLALCHEMY_DATABASE_URI = postgres_database_url
        STARTUP_DB_CHECKS = False
        CONFIG_ERRORS = ["Missing required environment variables."]

    temp_app = Flask("setup-config-errors")
    temp_app.config["SQLALCHEMY_DATABASE_URI"] = postgres_database_url
    temp_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(temp_app)
    with temp_app.app_context():
        db.create_all()

    monkeypatch.setenv("MIGRATE_ON_STARTUP", "false")
    app = create_app(ConfigWithErrors)
    client = app.test_client()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/setup" in response.headers.get("Location", "")

    setup_page = client.get("/setup")
    assert setup_page.status_code == 200

    with app.app_context():
        db.session.remove()
        db.drop_all()


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


def test_setup_validation_db_error_enables_maintenance_mode(
    monkeypatch: pytest.MonkeyPatch, postgres_database_url: str
) -> None:
    """Ensure setup validation errors enable maintenance mode safely.

    Args:
        monkeypatch: Pytest fixture used to override database query behavior.
        postgres_database_url: PostgreSQL connection string for tests.

    External dependencies:
        * Calls :func:`app.create_app` to construct the Flask application.
        * Overrides :class:`app.models.User` query behavior via ``monkeypatch``.
        * Initializes :data:`app.models.db` with a temporary
          :class:`flask.Flask` app via :meth:`flask_sqlalchemy.SQLAlchemy.init_app`.
    """

    class DummyQuery:
        """Provide a query stub that raises an OperationalError.

        External dependencies:
            * Raises :class:`sqlalchemy.exc.OperationalError` to emulate a
              database failure.
        """

        def count(self) -> int:
            """Raise an OperationalError to simulate database failure.

            Returns:
                Never returns because the method always raises.
            """

            raise OperationalError("SELECT 1", {}, Exception("db down"))

    class ErrorConfig(TestConfig):
        """Configuration overrides for database error validation.

        External dependencies:
            * Inherits from :class:`TestConfig` to reuse setup configuration.
        """

        SQLALCHEMY_DATABASE_URI = postgres_database_url

    temp_app = Flask("setup-validation")
    temp_app.config["SQLALCHEMY_DATABASE_URI"] = postgres_database_url
    temp_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(temp_app)
    with temp_app.app_context():
        monkeypatch.setattr(User, "query", DummyQuery())

    monkeypatch.setenv("MIGRATE_ON_STARTUP", "false")
    app = create_app(ErrorConfig)

    client = app.test_client()
    response = client.get("/healthz")
    assert response.status_code == 500


def test_setup_validation_error_with_config_errors_redirects_to_setup(
    monkeypatch: pytest.MonkeyPatch, postgres_database_url: str
) -> None:
    """Ensure config errors still redirect when setup validation fails.

    Args:
        monkeypatch: Pytest fixture used to override database query behavior.
        postgres_database_url: PostgreSQL connection string for tests.

    External dependencies:
        * Calls :func:`app.create_app` to construct the Flask application.
        * Overrides :class:`app.models.User` query behavior via ``monkeypatch``.
        * Initializes :data:`app.models.db` with a temporary
          :class:`flask.Flask` app via :meth:`flask_sqlalchemy.SQLAlchemy.init_app`.
    """

    class DummyQuery:
        """Provide a query stub that raises an OperationalError.

        External dependencies:
            * Raises :class:`sqlalchemy.exc.OperationalError` to emulate a
              database failure.
        """

        def count(self) -> int:
            """Raise an OperationalError to simulate database failure.

            Returns:
                Never returns because the method always raises.
            """

            raise OperationalError("SELECT 1", {}, Exception("db down"))

    class ErrorConfig(TestConfig):
        """Configuration overrides for config-only setup failures.

        External dependencies:
            * Inherits from :class:`TestConfig` to reuse setup configuration.
        """

        SQLALCHEMY_DATABASE_URI = postgres_database_url
        STARTUP_DB_CHECKS = False
        CONFIG_ERRORS = ["Missing database configuration."]

    temp_app = Flask("setup-config-errors")
    temp_app.config["SQLALCHEMY_DATABASE_URI"] = postgres_database_url
    temp_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(temp_app)
    with temp_app.app_context():
        monkeypatch.setattr(User, "query", DummyQuery())

    monkeypatch.setenv("MIGRATE_ON_STARTUP", "false")
    app = create_app(ErrorConfig)
    client = app.test_client()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/setup" in response.headers.get("Location", "")
