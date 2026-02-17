from __future__ import annotations

import pytest
from flask import Flask, current_app
from flask.testing import FlaskClient

import app as app_module
from app import create_app
from app.models import Quote, User, ZipZone, db
from app.services.settings import (
    QUOTE_EMAIL_SMTP_SETTING_KEY,
    reload_overrides,
    set_setting,
)


class TestQuoteEmailSmtpConfig:
    """Configuration overrides for SMTP quote email setting tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True
    QUOTE_EMAIL_SMTP_ENABLED = True


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

    TestQuoteEmailSmtpConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestQuoteEmailSmtpConfig)

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


def _create_super_admin() -> User:
    """Create and persist a super admin user for testing.

    Args:
        None.

    Returns:
        User: Newly created super admin account.

    External dependencies:
        * Persists the record via :mod:`app.models.db`.
        * Calls :meth:`app.models.User.set_password` to hash credentials.
    """

    admin = User(email="admin@freightservices.net", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    db.session.commit()
    return admin


def _create_quote_for_user(user: User) -> Quote:
    """Create a quote record owned by the provided user.

    Args:
        user: Authenticated user receiving quote ownership in tests.

    Returns:
        Quote: Persisted quote instance linked to ``user``.

    External dependencies:
        * Persists records via :mod:`app.models.db`.
    """

    quote = Quote(
        quote_id="SMTP-QUOTE-001",
        user_id=user.id,
        quote_type="air",
        origin="64101",
        destination="90210",
        weight=200.0,
        weight_method="Actual",
        pieces=1,
        total=400.0,
        actual_weight=200.0,
        dim_weight=180.0,
        quote_metadata='{"accessorial_total": 0.0, "accessorials": {}, "miles": 99.0}',
    )
    db.session.add(quote)
    db.session.commit()
    return quote


def test_send_email_blocked_when_setting_disabled(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block quote emails when the admin toggle is disabled."""

    admin = _create_super_admin()
    set_setting(QUOTE_EMAIL_SMTP_SETTING_KEY, "false")
    db.session.commit()
    reload_overrides(current_app)

    send_calls: list[tuple] = []
    monkeypatch.setattr(app_module, "get_distance_miles", lambda *_: 15.0)
    monkeypatch.setattr(
        app_module, "send_email", lambda *args, **kwargs: send_calls.append(args)
    )

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.post(
        "/send",
        data={
            "origin_zip": "64101",
            "destination_zip": "90210",
            "email": "customer@example.com",
        },
    )

    assert response.status_code == 302
    assert "/quotes/new" in response.headers["Location"]
    assert send_calls == []


def test_send_email_allowed_when_setting_enabled(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow quote emails when the admin toggle is enabled."""

    admin = _create_super_admin()
    set_setting(QUOTE_EMAIL_SMTP_SETTING_KEY, "true")
    db.session.commit()
    reload_overrides(current_app)

    quote = _create_quote_for_user(admin)
    db.session.add_all(
        [
            ZipZone(zipcode="64101", dest_zone=1, notes="Origin appointment required"),
            ZipZone(
                zipcode="90210",
                dest_zone=2,
                notes="Destination has limited receiving hours",
            ),
        ]
    )
    db.session.commit()

    send_calls: list[dict[str, object]] = []
    monkeypatch.setattr(app_module, "get_distance_miles", lambda *_: 22.0)
    monkeypatch.setattr(
        app_module,
        "send_email",
        lambda *args, **kwargs: send_calls.append({"args": args, "kwargs": kwargs}),
    )

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.post(
        "/send",
        data={
            "origin_zip": "64101",
            "destination_zip": "90210",
            "email": "customer@example.com",
            "quote_id": quote.quote_id,
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert send_calls
    assert send_calls[0]["kwargs"]["feature"] == "quote_email"
    assert (
        send_calls[0]["args"][1] == f"Freight Services Quote Details - {quote.quote_id}"
    )
    assert f"Quote ID: {quote.quote_id}" in str(send_calls[0]["args"][2])
    assert "Origin appointment required" in str(send_calls[0]["args"][2])
    assert "Destination has limited receiving hours" in str(send_calls[0]["args"][2])
    html_body = str(send_calls[0]["kwargs"]["html_body"])
    assert (
        "A message from Freight Services, Here is the quote information "
        "you were inquiring about." in html_body
    )
    assert "requested be sent to yourself" not in html_body
    assert "Mileage:" not in html_body
    assert "weight used for quote" in html_body
    assert "Origin appointment required" in html_body
    assert "Destination has limited receiving hours" in html_body


def test_nav_shows_create_quote_button_for_authenticated_users(app: Flask) -> None:
    """Show a prominent create-quote button in the header for signed-in users.

    Args:
        app: Flask application fixture backed by a PostgreSQL test database.

    Returns:
        None. Asserts against rendered navigation HTML.

    External dependencies:
        * Creates a super admin using :func:`_create_super_admin`.
        * Authenticates the browser session via :func:`_login_client`.
        * Calls :meth:`flask.testing.FlaskClient.get` to render the quote page.
    """

    admin = _create_super_admin()
    client = app.test_client()
    _login_client(client, admin.id)

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/quotes/new"' in html
    assert "Create New Quote" in html
    assert "btn btn-outline-primary" in html


def test_admin_zip_zone_form_and_list_display_notes(app: Flask) -> None:
    """Show shipment notes controls on ZIP zone admin pages.

    Args:
        app: Flask application fixture backed by a PostgreSQL test database.

    Returns:
        None. Performs HTML assertions against the form and list templates.

    External dependencies:
        * Creates a super admin using :func:`_create_super_admin`.
        * Authenticates the session with :func:`_login_client`.
        * Calls :meth:`flask.testing.FlaskClient.get` to render admin pages.
    """

    admin = _create_super_admin()
    db.session.add(
        ZipZone(zipcode="10001", dest_zone=1, beyond="N", notes="Dock side only")
    )
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)

    form_response = client.get("/admin/zip_zones/new")
    list_response = client.get("/admin/zip_zones")

    assert form_response.status_code == 200
    assert "Shipment Notes" in form_response.get_data(as_text=True)

    assert list_response.status_code == 200
    html = list_response.get_data(as_text=True)
    assert "Shipment Notes" in html
    assert "Dock side only" in html


def test_admin_zip_zone_create_persists_notes(app: Flask) -> None:
    """Persist shipment notes when creating ZIP zones from the admin form.

    Args:
        app: Flask application fixture backed by a PostgreSQL test database.

    Returns:
        None. Verifies a created :class:`app.models.ZipZone` stores notes.

    External dependencies:
        * Uses :func:`_create_super_admin` and :func:`_login_client` for auth.
        * Calls :meth:`flask.testing.FlaskClient.post` on ``/admin/zip_zones/new``.
        * Queries :class:`app.models.ZipZone` via :mod:`app.models.db`.
    """

    admin = _create_super_admin()
    client = app.test_client()
    _login_client(client, admin.id)

    response = client.post(
        "/admin/zip_zones/new",
        data={
            "zipcode": "73301",
            "dest_zone": 3,
            "beyond": "Y",
            "notes": "Call before delivery",
        },
    )

    assert response.status_code == 302
    created = ZipZone.query.filter_by(zipcode="73301").first()
    assert created is not None
    assert created.notes == "Call before delivery"
