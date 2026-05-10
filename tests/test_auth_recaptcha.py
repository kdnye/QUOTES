from __future__ import annotations

import uuid

from flask import Flask

from app import create_app
from app.models import User, db


class TestAuthRecaptchaConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False


def _register_payload() -> dict[str, str]:
    """Return a complete registration payload for route tests."""

    suffix = uuid.uuid4().hex[:8]
    return {
        "first_name": "Test",
        "last_name": "User",
        "email": f"test-{suffix}@example.com",
        "phone": "555-555-5555",
        "company_name": "ACME",
        "company_phone": "555-555-0000",
        "password": "ValidPass123!",
        "confirm_password": "ValidPass123!",
    }


def test_register_uses_math_challenge_when_recaptcha_disabled(
    postgres_database_url: str,
) -> None:
    TestAuthRecaptchaConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    app: Flask = create_app(TestAuthRecaptchaConfig)

    with app.app_context():
        client = app.test_client()
        response = client.get("/auth/register")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Human Verification:" in html
        assert "g-recaptcha" not in html


def test_register_rejects_failed_recaptcha(
    postgres_database_url: str,
    monkeypatch,
) -> None:
    TestAuthRecaptchaConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    app: Flask = create_app(TestAuthRecaptchaConfig)
    app.config["RECAPTCHA_SITE_KEY"] = "site-key"
    app.config["RECAPTCHA_SECRET_KEY"] = "secret-key"

    monkeypatch.setattr("app.auth._verify_recaptcha_response", lambda *_args: False)

    with app.app_context():
        client = app.test_client()
        response = client.post(
            "/auth/register",
            data={**_register_payload(), "g-recaptcha-response": "bad-token"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Human verification failed" in response.get_data(as_text=True)
        assert User.query.count() == 0
