from __future__ import annotations

import json

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import Quote, User, db


class TestQuoteEmailAccessConfig:
    """Configuration overrides for quote email access tests."""

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

    TestQuoteEmailAccessConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestQuoteEmailAccessConfig)

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


def test_email_request_form_allows_customer_user(app: Flask) -> None:
    """Allow a non-privileged customer to access the email request form."""

    customer = User(email="customer@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()

    quote = Quote(
        quote_type="Hotshot",
        origin="64101",
        destination="90210",
        weight=150.0,
        weight_method="Actual",
        total=200.0,
        quote_metadata=json.dumps({"accessorial_total": 0.0, "accessorials": {}}),
        user_id=customer.id,
        user_email=customer.email,
    )
    db.session.add(quote)
    db.session.commit()

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.get(f"/quotes/{quote.quote_id}/email")

    assert response.status_code == 200
    assert b"Email Booking Request" in response.data
