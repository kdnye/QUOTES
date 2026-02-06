from __future__ import annotations

import pytest
from flask import Flask

from app import create_app
from app.models import User, db
from app.services.mail import user_has_mail_privileges


class TestMailPrivilegesConfig:
    """Configuration overrides for mail privilege tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True
    MAIL_PRIVILEGED_DOMAIN = "freightservices.net"


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

    TestMailPrivilegesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestMailPrivilegesConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def test_mail_privileges_allow_opt_in(app: Flask) -> None:
    """Allow opt-in email privileges for users outside the privileged domain.

    Args:
        app: Flask test application configured with a privileged domain.

    Returns:
        None. The assertion verifies explicit opt-in grants access.

    External dependencies:
        * Persists :class:`app.models.User` records via :mod:`app.models.db`.
        * Calls :func:`app.services.mail.user_has_mail_privileges` for policy.
    """

    user = User(
        email="contractor@example.com",
        role="customer",
        can_send_mail=True,
    )
    db.session.add(user)
    db.session.commit()

    assert user_has_mail_privileges(user) is True


def test_mail_privileges_reject_missing_user(app: Flask) -> None:
    """Reject mail privileges when no authenticated user is supplied.

    Args:
        app: Flask test application for context access.

    Returns:
        None. The assertion confirms anonymous users are denied.

    External dependencies:
        * Uses :func:`app.services.mail.user_has_mail_privileges`.
    """

    assert user_has_mail_privileges(None) is False
