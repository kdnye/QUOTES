from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import User, db


class TestSettingsMailPrivilegesConfig:
    """Configuration overrides for account settings mail privilege tests."""

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

    TestSettingsMailPrivilegesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSettingsMailPrivilegesConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _login_client(client: FlaskClient, user_id: int) -> None:
    """Log in a user for the provided test client.

    Args:
        client: Flask test client that stores session data.
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


def _settings_payload(email: str, can_send_mail: bool) -> dict[str, str]:
    """Build a valid account settings form payload for POST requests.

    Args:
        email: Email value to submit for the account.
        can_send_mail: Whether to include the checkbox field in the payload.

    Returns:
        Dictionary keyed by :func:`app.auth.settings` form field names.

    External dependencies:
        * Mirrors required fields validated by :func:`app.auth.settings`.
    """

    payload = {
        "first_name": "Casey",
        "last_name": "Customer",
        "email": email,
        "phone": "+1 (555) 555-5555",
        "company_name": "Freight Services",
        "company_phone": "+1 (555) 555-0100",
        "current_password": "",
        "new_password": "",
        "confirm_password": "",
    }
    if can_send_mail:
        payload["can_send_mail"] = "on"
    return payload


def test_non_admin_settings_page_disables_mail_toggle(app: Flask) -> None:
    """Render a disabled mail privilege checkbox for non-admin users."""

    user = User(email="customer-settings@example.com", role="customer", is_admin=False)
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    _login_client(client, user.id)

    response = client.get("/settings")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="can_send_mail"' in page
    assert "This setting is managed by system administrators." in page
    assert 'id="can_send_mail"' in page and "disabled" in page


def test_non_admin_post_cannot_escalate_mail_privileges(app: Flask) -> None:
    """Ignore non-admin attempts to enable ``can_send_mail`` via form posts."""

    user = User(
        email="customer-update@example.com",
        role="customer",
        is_admin=False,
        can_send_mail=False,
    )
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    _login_client(client, user.id)

    response = client.post(
        "/settings",
        data=_settings_payload(user.email, can_send_mail=True),
        follow_redirects=False,
    )

    assert response.status_code == 302
    refreshed_user = db.session.get(User, user.id)
    assert refreshed_user is not None
    assert refreshed_user.can_send_mail is False


def test_admin_can_update_mail_privileges_from_settings(app: Flask) -> None:
    """Allow admins to update their own mail privilege toggle."""

    admin = User(
        email="admin-settings@example.com",
        role="super_admin",
        is_admin=True,
        can_send_mail=False,
    )
    admin.set_password("StrongPassw0rd!")
    db.session.add(admin)
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)

    response = client.post(
        "/settings",
        data=_settings_payload(admin.email, can_send_mail=True),
        follow_redirects=False,
    )

    assert response.status_code == 302
    refreshed_admin = db.session.get(User, admin.id)
    assert refreshed_admin is not None
    assert refreshed_admin.can_send_mail is True
