from __future__ import annotations

import smtplib
import sys
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import Mock

import pytest
from flask import Flask, url_for
from flask.testing import FlaskClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app
from app.models import User, db
from app.services.mail import MailRateLimitError


class TestResetRequestConfig:
    """Configuration overrides for password reset request tests."""

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

    TestResetRequestConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestResetRequestConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _login_client(client: FlaskClient, user_id: int) -> None:
    """Log in a user for the provided test client.

    Args:
        client: Flask test client that will store the session data.
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


def test_reset_request_sends_email(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure reset requests send a password reset email.

    Args:
        app: Flask application configured for tests.
        monkeypatch: Fixture used to mock reset token creation and email sends.

    Returns:
        None. Assertions verify reset email data and parameters.

    External dependencies:
        * Uses :func:`app.auth.create_reset_token` to generate tokens.
        * Calls :func:`app.services.mail.send_email` to deliver messages.
    """

    user = User(email="resetter@example.com", role="customer")
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    _login_client(client, user.id)

    token_value = "token-123"

    def _fake_create_reset_token(email: str) -> Tuple[Optional[str], Optional[str]]:
        """Return a deterministic reset token for testing.

        Args:
            email: Email address associated with the reset request.

        Returns:
            Tuple of the fake token and ``None`` for the error message.
        """

        assert email == user.email
        return token_value, None

    send_email_mock = Mock()

    monkeypatch.setattr("app.auth.create_reset_token", _fake_create_reset_token)
    monkeypatch.setattr("app.auth.send_email", send_email_mock)

    response = client.post("/reset", follow_redirects=True)

    assert response.status_code == 200
    send_email_mock.assert_called_once()

    args, kwargs = send_email_mock.call_args
    assert args[0] == user.email
    subject = args[1]
    body = args[2]
    with app.test_request_context():
        expected_url = url_for(
            "auth.reset_with_token", token=token_value, _external=True
        )
    assert expected_url in body
    assert "reset" in subject.lower()
    assert kwargs["feature"] == "password_reset"
    assert kwargs["user"].id == user.id


@pytest.mark.parametrize(
    "exception",
    [
        MailRateLimitError("rate limited"),
        smtplib.SMTPException("smtp failed"),
    ],
)
def test_reset_request_handles_email_failures(
    app: Flask, monkeypatch: pytest.MonkeyPatch, exception: Exception
) -> None:
    """Ensure reset requests handle email delivery failures gracefully.

    Args:
        app: Flask application configured for tests.
        monkeypatch: Fixture used to mock reset token creation and email sends.
        exception: Exception instance raised when sending email fails.

    Returns:
        None. Assertions verify the error is surfaced safely.

    External dependencies:
        * Uses :func:`app.auth.create_reset_token` to generate tokens.
        * Calls :func:`app.services.mail.send_email` which raises ``exception``.
    """

    user = User(email="limited@example.com", role="customer")
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    _login_client(client, user.id)

    def _fake_create_reset_token(email: str) -> Tuple[Optional[str], Optional[str]]:
        """Return a deterministic reset token for testing.

        Args:
            email: Email address associated with the reset request.

        Returns:
            Tuple of the fake token and ``None`` for the error message.
        """

        assert email == user.email
        return "token-456", None

    def _raise_send_email(*args: object, **kwargs: object) -> None:
        """Raise the provided ``exception`` to simulate SMTP failure.

        Args:
            *args: Positional arguments passed to the mock function.
            **kwargs: Keyword arguments passed to the mock function.

        Returns:
            None. This helper always raises ``exception``.
        """

        raise exception

    send_email_mock = Mock(side_effect=_raise_send_email)

    monkeypatch.setattr("app.auth.create_reset_token", _fake_create_reset_token)
    monkeypatch.setattr("app.auth.send_email", send_email_mock)

    response = client.post("/reset", follow_redirects=True)

    assert response.status_code == 200
    send_email_mock.assert_called_once()
    assert b"We couldn't send the reset email right now." in response.data
