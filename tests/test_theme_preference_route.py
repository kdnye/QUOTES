from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import User, db


class TestThemePreferenceConfig:
    """Configuration overrides for theme preference persistence tests."""

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

    TestThemePreferenceConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestThemePreferenceConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _login_client(client: FlaskClient, user_id: int) -> None:
    """Log in a user for the provided test client.

    Args:
        client: Flask test client that will store session data.
        user_id: Primary key of the :class:`app.models.User` to authenticate.

    Returns:
        None. The client session is updated with Flask-Login keys.

    External dependencies:
        * Uses :func:`flask.testing.FlaskClient.session_transaction` to modify
          the session expected by :mod:`flask_login`.
    """

    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_update_theme_persists_manual_override(app: Flask) -> None:
    """Persist valid theme values posted to the theme preference endpoint."""

    user = User(email="theme-user@example.com", role="customer")
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    _login_client(client, user.id)

    response = client.post("/settings/theme", json={"theme": "dark"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "theme": "dark"}

    refreshed_user = db.session.get(User, user.id)
    assert refreshed_user is not None
    assert refreshed_user.theme_preference == "dark"


def test_update_theme_rejects_unknown_values(app: Flask) -> None:
    """Default invalid theme values to ``auto`` for safe persistence."""

    user = User(email="invalid-theme@example.com", role="customer")
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    _login_client(client, user.id)

    response = client.post("/settings/theme", json={"theme": "sepia"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "theme": "auto"}

    refreshed_user = db.session.get(User, user.id)
    assert refreshed_user is not None
    assert refreshed_user.theme_preference == "auto"
