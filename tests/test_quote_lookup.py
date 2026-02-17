from __future__ import annotations

import json
import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import Quote, User, db


class TestQuoteLookupConfig:
    """Configuration overrides for quote lookup integration tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create a Flask app wired to the PostgreSQL test database.

    Args:
        postgres_database_url: PostgreSQL connection string used for test runs.
        monkeypatch: Fixture used to configure environment variables.

    Returns:
        A Flask app configured for quote lookup route tests.

    External dependencies:
        * Sets ``MIGRATE_ON_STARTUP`` using :func:`pytest.MonkeyPatch.setenv`.
        * Calls :func:`app.create_app` to create the Flask application.
    """

    TestQuoteLookupConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestQuoteLookupConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _login_client(client: FlaskClient, user_id: int) -> None:
    """Log in a test user by writing expected Flask-Login session values.

    Args:
        client: Flask test client whose session should be updated.
        user_id: Primary key for the :class:`app.models.User` being authenticated.

    Returns:
        None. Session data is updated in-place.

    External dependencies:
        * Uses :func:`flask.testing.FlaskClient.session_transaction` to set
          ``_user_id`` and ``_fresh`` expected by :mod:`flask_login`.
    """

    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _create_user_and_login(client: FlaskClient) -> User:
    """Create a customer user and authenticate the supplied client.

    Args:
        client: Flask client that should be logged in for protected routes.

    Returns:
        Persisted :class:`app.models.User` instance.

    External dependencies:
        * Uses :data:`app.models.db.session` to persist the user.
        * Calls :func:`_login_client` to establish authenticated session state.
    """

    user = User(email=f"lookup-{uuid.uuid4()}@example.com", role="customer")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    _login_client(client, user.id)
    return user


def _create_quote(
    user: User,
    quote_id: str,
    *,
    quote_metadata: str,
    total: float = 210.0,
) -> Quote:
    """Persist a quote record with deterministic values for HTML assertions.

    Args:
        user: Owner of the quote.
        quote_id: Public readable ID string used by ``/quotes/lookup``.
        quote_metadata: JSON or malformed text saved on ``Quote.quote_metadata``.
        total: Quote total used by ``quote_result.html`` calculations.

    Returns:
        Saved :class:`app.models.Quote` record.

    External dependencies:
        * Uses :data:`app.models.db.session` to save the quote.
        * Supplies fields rendered by ``templates/quote_result.html``.
    """

    quote = Quote(
        quote_id=quote_id,
        quote_type="Hotshot",
        origin="30301",
        destination="60601",
        weight=500.0,
        weight_method="Actual",
        total=total,
        quote_metadata=quote_metadata,
        user_id=user.id,
        user_email=user.email,
    )
    db.session.add(quote)
    db.session.commit()
    return quote


def test_lookup_quote_get_renders_heading_for_authenticated_user(app: Flask) -> None:
    """Authenticated users should see the lookup form heading on GET requests."""

    client = app.test_client()
    _create_user_and_login(client)

    response = client.get("/quotes/lookup")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Find Existing Quote" in html
    assert "Quote ID" in html


def test_lookup_quote_post_invalid_uuid_shows_validation_flash(app: Flask) -> None:
    """POST requests with invalid IDs should render danger flash feedback."""

    client = app.test_client()
    _create_user_and_login(client)

    response = client.post("/quotes/lookup", data={"quote_id": "not-a-uuid"})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Please enter a valid Quote ID." in html
    assert 'class="alert alert-danger"' in html


def test_lookup_quote_post_missing_quote_shows_not_found_flash(app: Flask) -> None:
    """Valid readable IDs without matching rows should show not-found flash."""

    client = app.test_client()
    _create_user_and_login(client)

    response = client.post("/quotes/lookup", data={"quote_id": "Q-ZYXWVUT2"})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Quote not found. Please verify the Quote ID and try again." in html
    assert 'class="alert alert-danger"' in html


def test_lookup_quote_post_existing_quote_renders_expected_result_fields(
    app: Flask,
) -> None:
    """Existing quote IDs should render core quote details and the total section."""

    client = app.test_client()
    user = _create_user_and_login(client)
    quote = _create_quote(
        user,
        "Q-HJKMNP23",
        quote_metadata=json.dumps({"accessorial_total": 10.0, "pieces": 3}),
        total=210.0,
    )

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Quote Result" in html
    assert "Origin: 30301 | Destination: 60601" in html
    assert "Quote Total" in html
    assert "$210.00" in html
    assert "Pieces: 3" in html


def test_lookup_quote_post_malformed_metadata_renders_without_server_error(
    app: Flask,
) -> None:
    """Malformed ``quote_metadata`` should gracefully fall back to empty metadata."""

    client = app.test_client()
    user = _create_user_and_login(client)
    quote = _create_quote(
        user,
        "Q-HJKMNP23",
        quote_metadata="not-json-text",
        total=199.99,
    )

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Quote Result" in html
    assert "Origin: 30301 | Destination: 60601" in html
    assert "Quote Total" in html
    assert "$199.99" in html
