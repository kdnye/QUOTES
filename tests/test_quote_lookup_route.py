from __future__ import annotations

import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import Quote, User, db


class TestQuoteLookupConfig:
    """Configuration overrides for quote lookup route tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create a Flask app connected to the PostgreSQL test database.

    Args:
        postgres_database_url: PostgreSQL connection string for tests.
        monkeypatch: Fixture used to set migration startup behavior.

    Returns:
        Flask application configured for route testing.

    External dependencies:
        * Calls :func:`app.create_app` to construct the Flask application.
        * Writes ``MIGRATE_ON_STARTUP`` through :func:`pytest.MonkeyPatch.setenv`.
    """

    TestQuoteLookupConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestQuoteLookupConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _login_client(client: FlaskClient, user_id: int) -> None:
    """Authenticate a test client for routes protected by Flask-Login.

    Args:
        client: Flask test client with mutable session storage.
        user_id: Database identifier of the user to authenticate.

    Returns:
        None. Updates the session keys expected by Flask-Login.

    External dependencies:
        * Uses :meth:`flask.testing.FlaskClient.session_transaction` to mutate
          ``_user_id`` and ``_fresh``.
    """

    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _create_user_and_login(client: FlaskClient) -> User:
    """Create a customer user and sign them in for lookup route requests.

    Args:
        client: Flask test client that should be authenticated.

    Returns:
        Persisted :class:`app.models.User` record used for request ownership.

    External dependencies:
        * Uses :data:`app.models.db.session` for persistence.
        * Calls :func:`_login_client` to establish authenticated session state.
    """

    user = User(email=f"lookup-{uuid.uuid4()}@example.com", role="customer")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    _login_client(client, user.id)
    return user


def test_lookup_quote_get_renders_lookup_template(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render the lookup page for authenticated users on GET requests."""

    client = app.test_client()
    _create_user_and_login(client)

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )

    response = client.get("/quotes/lookup")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=lookup_quote.html"


def test_lookup_quote_post_invalid_uuid_renders_lookup_with_flash(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show a validation error when users submit a malformed quote ID."""

    client = app.test_client()
    _create_user_and_login(client)

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )

    response = client.post("/quotes/lookup", data={"quote_id": "not-a-uuid"})

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=lookup_quote.html"
    with client.session_transaction() as session:
        assert ("danger", "Please enter a valid Quote ID.") in session["_flashes"]


def test_lookup_quote_post_missing_quote_renders_lookup_with_not_found_flash(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show a not-found flash when no quote matches a valid readable ID."""

    client = app.test_client()
    _create_user_and_login(client)

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )

    response = client.post("/quotes/lookup", data={"quote_id": "Q-ABCDEFGH"})

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=lookup_quote.html"
    with client.session_transaction() as session:
        assert (
            "danger",
            "Quote not found. Please verify the Quote ID and try again.",
        ) in session["_flashes"]


def test_lookup_quote_post_found_quote_renders_result_context(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render quote results with metadata fallback and email permission flags."""

    client = app.test_client()
    user = _create_user_and_login(client)

    quote = Quote(
        quote_id="Q-BCDFGHJ2",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=150.0,
        total=325.0,
        quote_metadata="{invalid-json",
        user_id=user.id,
        user_email=user.email,
    )
    db.session.add(quote)
    db.session.commit()

    captured: dict[str, object] = {}

    def _fake_render(template_name: str, **context: object) -> str:
        captured["template_name"] = template_name
        captured["context"] = context
        return f"template={template_name}"

    monkeypatch.setattr("app.quotes.routes.render_template", _fake_render)
    monkeypatch.setattr("app.quotes.routes.check_thresholds", lambda *_: "warn")
    monkeypatch.setattr("app.quotes.routes.is_quote_email_smtp_enabled", lambda: True)
    monkeypatch.setattr("app.quotes.routes.user_has_mail_privileges", lambda _: True)

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"
    assert captured["template_name"] == "quote_result.html"
    context = captured["context"]
    assert context["quote"].id == quote.id
    assert context["metadata"] == {}
    assert context["exceeds_threshold"] is True
    assert context["can_request_booking_email"] is True
    assert context["quote_email_smtp_enabled"] is True
    assert context["user_can_send_quote_email"] is True
    assert context["can_send_quote_email"] is True


def test_lookup_quote_post_lowercase_quote_id_normalizes_to_match(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lookup should normalize lowercase IDs before querying the database."""

    client = app.test_client()
    user = _create_user_and_login(client)

    quote = Quote(
        quote_id="Q-PLMNBV23",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=120.0,
        total=300.0,
        quote_metadata="{}",
        user_id=user.id,
        user_email=user.email,
    )
    db.session.add(quote)
    db.session.commit()

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )
    monkeypatch.setattr("app.quotes.routes.is_quote_email_smtp_enabled", lambda: True)
    monkeypatch.setattr("app.quotes.routes.user_has_mail_privileges", lambda _: True)

    response = client.post("/quotes/lookup", data={"quote_id": " q-plmnbv23 "})

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"
