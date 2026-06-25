from __future__ import annotations

import smtplib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import Mock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app
from app.models import EmailOtpToken, User, db
from app.services.mail import MailRateLimitError
from app.services import two_factor

FIXED_CODE = "135790"


class TwoFactorTestConfig:
    """Configuration overrides for email two-factor login tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True
    TWO_FACTOR_ENABLED = True
    # Deterministic windows so tests don't depend on wall-clock spacing.
    TWO_FACTOR_RESEND_COOLDOWN_SECONDS = 0
    TWO_FACTOR_MAX_ATTEMPTS = 3
    TWO_FACTOR_CODE_TTL_MINUTES = 10
    # Keep the limiter from interfering with multi-request tests.
    AUTH_2FA_VERIFY_RATE_LIMIT = "1000 per minute"
    AUTH_2FA_RESEND_RATE_LIMIT = "1000 per minute"
    AUTH_LOGIN_RATE_LIMIT = "1000 per minute"


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create a Flask app wired to a PostgreSQL test database."""

    TwoFactorTestConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    application = create_app(TwoFactorTestConfig)
    with application.app_context():
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def send_mock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace the 2FA email transport with a Mock and fix the code value."""

    mock = Mock()
    monkeypatch.setattr(two_factor, "send_email", mock)
    monkeypatch.setattr(two_factor, "_generate_numeric_code", lambda length: FIXED_CODE)
    return mock


def _make_user(
    *, password: str = "StrongPassw0rd!", two_factor_enabled: bool = True
) -> User:
    """Persist and return a customer user for login tests."""

    user = User(
        email=f"user-{datetime.utcnow().timestamp()}@example.com",
        role="customer",
        two_factor_enabled=two_factor_enabled,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _session_user_id(client) -> Optional[str]:
    """Return the Flask-Login user id stored on the client session, if any."""

    with client.session_transaction() as sess:
        return sess.get("_user_id")


def _pending_id(client) -> Optional[int]:
    with client.session_transaction() as sess:
        return sess.get("pending_2fa_user_id")


def test_password_login_starts_email_challenge(app: Flask, send_mock: Mock) -> None:
    """A valid password should email a code instead of logging the user in."""

    user = _make_user()
    client = app.test_client()

    response = client.post(
        "/login",
        data={"email": user.email, "password": "StrongPassw0rd!"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login/verify")
    # Not logged in yet — only pending.
    assert _session_user_id(client) is None
    assert _pending_id(client) == user.id
    # A code was emailed via the 2FA feature, to the user's address.
    send_mock.assert_called_once()
    args, kwargs = send_mock.call_args
    assert args[0] == user.email
    assert kwargs["feature"] == two_factor.TWO_FACTOR_FEATURE
    assert FIXED_CODE in args[2]  # plain-text body carries the code
    # Exactly one active code persisted, stored only as a hash.
    tokens = EmailOtpToken.query.filter_by(user_id=user.id).all()
    assert len(tokens) == 1
    assert tokens[0].code_hash == two_factor.hash_login_code(FIXED_CODE)
    assert tokens[0].code_hash != FIXED_CODE


def test_correct_code_completes_login(app: Flask, send_mock: Mock) -> None:
    """Submitting the emailed code finishes the login and consumes the code."""

    user = _make_user()
    client = app.test_client()
    client.post("/login", data={"email": user.email, "password": "StrongPassw0rd!"})

    response = client.post(
        "/login/verify", data={"code": FIXED_CODE}, follow_redirects=False
    )

    assert response.status_code == 302
    assert _session_user_id(client) == str(user.id)
    assert _pending_id(client) is None
    token = EmailOtpToken.query.filter_by(user_id=user.id).first()
    assert token.used is True


def test_wrong_code_rejected_and_counts_attempts(app: Flask, send_mock: Mock) -> None:
    """An incorrect code keeps the user out and records the failed attempt."""

    user = _make_user()
    client = app.test_client()
    client.post("/login", data={"email": user.email, "password": "StrongPassw0rd!"})

    response = client.post(
        "/login/verify", data={"code": "000000"}, follow_redirects=True
    )

    assert response.status_code == 200
    assert b"Incorrect code" in response.data
    assert _session_user_id(client) is None
    token = EmailOtpToken.query.filter_by(user_id=user.id).first()
    assert token.attempts == 1
    assert token.used is False


def test_code_burned_after_max_attempts(app: Flask, send_mock: Mock) -> None:
    """The active code is consumed once the wrong-guess cap is reached."""

    user = _make_user()
    client = app.test_client()
    client.post("/login", data={"email": user.email, "password": "StrongPassw0rd!"})

    for _ in range(TwoFactorTestConfig.TWO_FACTOR_MAX_ATTEMPTS):
        client.post("/login/verify", data={"code": "000000"})

    token = EmailOtpToken.query.filter_by(user_id=user.id).first()
    assert token.used is True
    # Even the correct code now fails because the token is burned.
    response = client.post(
        "/login/verify", data={"code": FIXED_CODE}, follow_redirects=True
    )
    assert _session_user_id(client) is None
    assert b"Request a new" in response.data


def test_expired_code_rejected(app: Flask, send_mock: Mock) -> None:
    """A code past its expiry cannot be used to log in."""

    user = _make_user()
    client = app.test_client()
    client.post("/login", data={"email": user.email, "password": "StrongPassw0rd!"})

    token = EmailOtpToken.query.filter_by(user_id=user.id).first()
    token.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()

    response = client.post(
        "/login/verify", data={"code": FIXED_CODE}, follow_redirects=True
    )
    assert _session_user_id(client) is None
    assert b"expired" in response.data


def test_global_flag_disables_two_factor(app: Flask, send_mock: Mock) -> None:
    """With the deployment flag off, password login signs in directly."""

    app.config["TWO_FACTOR_ENABLED"] = False
    user = _make_user()
    client = app.test_client()

    response = client.post(
        "/login",
        data={"email": user.email, "password": "StrongPassw0rd!"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert not response.headers["Location"].endswith("/login/verify")
    assert _session_user_id(client) == str(user.id)
    send_mock.assert_not_called()
    assert EmailOtpToken.query.count() == 0


def test_user_toggle_disables_two_factor(app: Flask, send_mock: Mock) -> None:
    """A user with two_factor_enabled=False logs in without a code."""

    user = _make_user(two_factor_enabled=False)
    client = app.test_client()

    response = client.post(
        "/login",
        data={"email": user.email, "password": "StrongPassw0rd!"},
        follow_redirects=False,
    )

    assert _session_user_id(client) == str(user.id)
    send_mock.assert_not_called()


def test_resend_issues_new_code_and_invalidates_old(
    app: Flask, send_mock: Mock
) -> None:
    """Resending emails a fresh code and retires the previous one."""

    user = _make_user()
    client = app.test_client()
    client.post("/login", data={"email": user.email, "password": "StrongPassw0rd!"})
    first = EmailOtpToken.query.filter_by(user_id=user.id).one()

    response = client.post("/login/verify/resend", follow_redirects=True)

    assert response.status_code == 200
    assert send_mock.call_count == 2
    db.session.refresh(first)
    assert first.used is True  # old code retired
    active = EmailOtpToken.query.filter_by(user_id=user.id, used=False).all()
    assert len(active) == 1


def test_verify_without_pending_session_redirects_to_login(app: Flask) -> None:
    """Hitting the verify page with no challenge bounces back to login."""

    _make_user()  # ensure the app is past first-run setup
    client = app.test_client()
    response = client.get("/login/verify", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_resend_cooldown_blocks_rapid_requests(app: Flask, send_mock: Mock) -> None:
    """The resend cooldown prevents back-to-back code emails."""

    app.config["TWO_FACTOR_RESEND_COOLDOWN_SECONDS"] = 300
    user = _make_user()
    client = app.test_client()
    client.post("/login", data={"email": user.email, "password": "StrongPassw0rd!"})
    assert send_mock.call_count == 1

    response = client.post("/login/verify/resend", follow_redirects=True)
    assert response.status_code == 200
    # No second email was sent while within the cooldown window.
    assert send_mock.call_count == 1
    assert b"wait a few seconds" in response.data


def test_mail_failure_during_login_is_handled(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SMTP failures while sending a code surface a friendly error."""

    monkeypatch.setattr(two_factor, "_generate_numeric_code", lambda length: FIXED_CODE)
    monkeypatch.setattr(
        two_factor, "send_email", Mock(side_effect=smtplib.SMTPException("boom"))
    )
    user = _make_user()
    client = app.test_client()

    response = client.post(
        "/login",
        data={"email": user.email, "password": "StrongPassw0rd!"},
        follow_redirects=True,
    )

    assert _session_user_id(client) is None
    # Apostrophe is HTML-escaped in the flash, so match the unambiguous tail.
    assert b"send your login code" in response.data


def test_rate_limit_error_during_login_is_handled(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mail rate-limit errors while sending a code are surfaced to the user."""

    monkeypatch.setattr(two_factor, "_generate_numeric_code", lambda length: FIXED_CODE)
    monkeypatch.setattr(
        two_factor,
        "send_email",
        Mock(side_effect=MailRateLimitError("slow down")),
    )
    user = _make_user()
    client = app.test_client()

    response = client.post(
        "/login",
        data={"email": user.email, "password": "StrongPassw0rd!"},
        follow_redirects=True,
    )

    assert _session_user_id(client) is None
    assert b"slow down" in response.data


def test_mask_email_hides_local_part() -> None:
    """The masked email keeps the domain but hides most of the local part."""

    assert two_factor.mask_email("jane.doe@example.com").endswith("@example.com")
    assert two_factor.mask_email("jane.doe@example.com") != "jane.doe@example.com"
    assert two_factor.mask_email("ab@x.com") == "a*@x.com"
