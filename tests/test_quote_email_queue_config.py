from __future__ import annotations

import pytest
from flask import Flask

from app import create_app


class QueueValidationConfig:
    """Configuration used to test production queue validation for quote emails.

    Inputs:
        None. Uses class attributes consumed by :func:`app.create_app`.

    Outputs:
        Provides the minimum Flask settings needed to initialize the app.

    External dependencies:
        * Read by :func:`app.create_app` during startup checks.
    """

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False
    QUOTE_EMAIL_SMTP_ENABLED = True
    CONFIG_ERRORS: list[str] = []


def test_create_app_records_config_error_when_prod_queue_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require ``CELERY_BROKER_URL`` in production when quote emails are enabled.

    Args:
        monkeypatch: Pytest fixture used to define environment variables.

    Returns:
        None. Assertions verify startup config errors and maintenance redirect.

    External dependencies:
        * Calls :func:`app.create_app` to initialize application startup checks.
        * Sends HTTP requests through :meth:`flask.Flask.test_client`.
    """

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    app: Flask = create_app(QueueValidationConfig)
    assert (
        "CELERY_BROKER_URL must be set in production when "
        "QUOTE_EMAIL_SMTP_ENABLED is true."
    ) in app.config["CONFIG_ERROR_DETAILS"]

    client = app.test_client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/setup" in response.headers.get("Location", "")
