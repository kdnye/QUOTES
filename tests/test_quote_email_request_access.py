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


def _create_quote_for_user(user: User) -> Quote:
    """Create and persist a quote record for email request view tests.

    Args:
        user: :class:`app.models.User` instance that owns the generated quote.

    Returns:
        Saved :class:`app.models.Quote` instance with minimal metadata
        required by ``quotes.email_request_form``.

    External dependencies:
        * Uses :data:`app.models.db.session` to persist the quote.
        * Mirrors metadata shape expected by ``app.quotes.routes._render_email_request``.
    """

    quote = Quote(
        quote_type="Hotshot",
        origin="64101",
        destination="90210",
        weight=150.0,
        weight_method="Actual",
        total=200.0,
        quote_metadata=json.dumps({"accessorial_total": 0.0, "accessorials": {}}),
        user_id=user.id,
        user_email=user.email,
    )
    db.session.add(quote)
    db.session.commit()
    return quote


def test_email_request_form_allows_customer_user(app: Flask) -> None:
    """Allow a non-privileged customer to access the email request form."""

    customer = User(email="customer@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()

    quote = _create_quote_for_user(customer)

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.get(f"/quotes/{quote.quote_id}/email")

    assert response.status_code == 200
    assert b"Email Booking Request" in response.data
    assert b"operations@freightservices.net" in response.data


@pytest.mark.parametrize(
    ("path_suffix", "expected_heading"),
    [
        ("email", "Email Booking Request"),
        ("email-volume", "Email Volume Pricing Request"),
    ],
)
def test_email_request_form_includes_return_quote_checkbox_and_email_body_line(
    app: Flask, path_suffix: str, expected_heading: str
) -> None:
    """Ensure both request variants render return quote controls in shared template.

    Args:
        app: Test Flask app fixture with database bindings.
        path_suffix: Route suffix selecting booking or volume variant.
        expected_heading: Heading expected for the selected route variant.

    Returns:
        None. Assertions validate rendered HTML and body composition script.

    External dependencies:
        * Calls ``quotes.email_request_form`` and ``quotes.email_volume_request_form``
          through :meth:`flask.testing.FlaskClient.get`.
        * Validates JavaScript embedded by ``templates/email_request.html``.
    """

    customer = User(email=f"return-{path_suffix}@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()

    quote = _create_quote_for_user(customer)

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.get(f"/quotes/{quote.quote_id}/{path_suffix}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert expected_heading in html
    assert 'id="shipper_notes"' in html
    assert 'name="shipper_notes"' in html
    assert 'name="shipper_reference"' in html
    assert 'id="consignee_notes"' in html
    assert 'name="consignee_notes"' in html
    assert 'name="consignee_reference"' in html
    assert 'id="pickup_date"' in html
    assert 'name="pickup_date"' in html
    assert 'id="delivery_date"' in html
    assert 'name="delivery_date"' in html
    assert 'id="return_quote_requested"' in html
    assert 'name="return_quote_requested"' in html
    assert 'id="return_pickup_date"' in html
    assert 'name="return_pickup_date"' in html
    assert 'id="return_accessorial"' in html
    assert 'name="return_accessorial"' in html
    assert 'type="checkbox"' in html
    assert "Select return accessorial" not in html
    assert 'id="return_notes"' in html
    assert 'name="return_notes"' in html
    assert (
        "WILL YOU NEED A RETURN SHIPMENT/RETURN HANDLING REQUESTED "
        "(FSI WILL REPLY WITH RETURN QUOTE)" in html
    )
    assert html.index('id="pickup_date"') < html.index('id="shipper_notes"')
    assert html.index('id="consignee_notes"') < html.index(
        'id="return_quote_requested"'
    )
    assert html.index('id="return_quote_requested"') < html.index("Compose Email")
    assert "const pickupDate = f.pickup_date.value;" in html
    assert "const deliveryDate = f.delivery_date.value || 'Not specified';" in html
    assert "const returnQuoteRequested = f.return_quote_requested.checked;" in html
    assert "reference: f.shipper_reference.value," in html
    assert "reference: f.consignee_reference.value," in html
    assert "normalizeFreeformNotes(f.shipper_notes.value)" in html
    assert "normalizeFreeformNotes(f.consignee_notes.value)" in html
    assert "normalizeFreeformNotes(f.return_notes.value)" in html
    assert (
        "returnShipmentDetails.classList.toggle('d-none', !returnQuoteCheckbox.checked);"
        in html
    )
    assert "[ RETURN SHIPMENT REQUEST ]" in html
    assert "Pickup Date:" in html
    assert "Delivery Date:" in html
    assert "Desired Pickup Date:" in html
    assert "Return Accessorial:" in html
    assert "Shipper Reference:" in html
    assert "Consignee Reference:" in html


def test_email_request_form_includes_maps_bootstrap_when_key_present(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render Google Places bootstrap script when a Maps API key is configured.

    Args:
        app: Test Flask app fixture with database bindings.
        monkeypatch: Fixture used to set environment variables for this test.

    Returns:
        None. Assertions validate rendered HTML content.

    External dependencies:
        * Calls ``quotes.email_request_form`` using :meth:`flask.testing.FlaskClient.get`.
        * Relies on ``app.quotes.routes._render_email_request`` to resolve key fallback order.
    """

    monkeypatch.setenv("MAPS_API_KEY", "maps-from-env")
    customer = User(email="maps@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()
    quote = _create_quote_for_user(customer)

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.get(f"/quotes/{quote.quote_id}/email")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "maps.googleapis.com/maps/api/js?key=maps-from-env&libraries=places" in html
    assert "callback=initAddressAutocomplete" in html
    assert "new google.maps.places.Autocomplete" in html


def test_email_request_form_omits_maps_script_when_key_missing(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip Google Places script when no API key is available.

    Args:
        app: Test Flask app fixture with database bindings.

    Returns:
        None. Assertions validate graceful template fallback behavior.

    External dependencies:
        * Calls ``quotes.email_request_form`` through Flask test client routing.
        * Validates HTML produced by ``templates/email_request.html``.
    """

    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("MAPS_API_KEY", raising=False)
    app.config["GOOGLE_MAPS_API_KEY"] = ""

    customer = User(email="nomaps@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()
    quote = _create_quote_for_user(customer)

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.get(f"/quotes/{quote.quote_id}/email")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "maps.googleapis.com/maps/api/js" not in html
    assert "function initAddressAutocomplete()" in html


def test_email_self_route_sends_quote_copy_and_flashes_success(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Send a quote copy to the logged-in user and include unsubscribe headers.

    Args:
        app: Test Flask app fixture with database bindings.
        monkeypatch: Fixture used to intercept outbound mail calls.

    Returns:
        None. Assertions validate send arguments and rendered response content.

    External dependencies:
        * Calls ``quotes.email_quote_to_me`` through
          :meth:`flask.testing.FlaskClient.post`.
        * Stubs :func:`app.quotes.routes.send_email` to avoid SMTP activity.
    """

    customer = User(email="self-send@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()

    quote = _create_quote_for_user(customer)
    quote.quote_metadata = json.dumps(
        {
            "accessorial_total": 470.0,
            "accessorials": {
                "PickUp After Hours (17:01-07:59)": 110.0,
                "Weekend PickUp": 125.0,
                "Delivery After Hours (17:01-07:59)": 110.0,
                "Weekend Delivery": 125.0,
            },
            "pieces": 2,
            "miles": 1250.5,
        }
    )
    quote.pieces = 2
    quote.actual_weight = 200.0
    quote.dim_weight = 260.0
    quote.weight = 260.0
    quote.weight_method = "Dimensional"
    quote.total = 1225.0
    db.session.commit()

    sent: dict[str, object] = {}

    def _fake_send_email(**kwargs: object) -> None:
        sent.update(kwargs)

    monkeypatch.setattr("app.quotes.routes.send_email", _fake_send_email)

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.post(
        f"/quotes/{quote.quote_id}/email-self",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Email a Copy to Myself" in html
    assert sent["to"] == customer.email
    assert sent["subject"] == f"Freight Services Quote Details - {quote.quote_id}"
    assert sent["feature"] == "quote_copy"
    assert sent["headers"] == {
        "List-Unsubscribe": "<https://quote.freightservices.net/help>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert "html_body" in sent
    html_body = str(sent["html_body"])
    assert (
        "Here's the copy of your quote you requested be sent to yourself." in html_body
    )
    assert "A message from Freight Services" not in html_body
    assert "Accessorials Total: $470.00" in html_body
    assert "Dimensional Weight:" in html_body
    assert "(weight used for quote)" in html_body
    body = str(sent["body"])
    assert f"Quote ID: {quote.quote_id}" in body
    assert "Return Quote:" not in body
    assert "Base Charge: $ 755.00" in body
    assert "Origin ZIP: 64101" in body
    assert "Destination ZIP: 90210" in body
    assert "Mileage: 1,250.50" in body
    assert "Actual Weight: 200.00 lbs" in body
    assert "Dimensional Weight: 260.00 lbs  <-- WEIGHT USED FOR QUOTE" in body
    assert "Billable Weight: 260.00 lbs" in body
    assert "Accessorials Total: $ 470.00" in body
    assert "Quote Total: $ 1225.00" in body

    with client.session_transaction() as session:
        assert ("success", f"Quote details sent to {customer.email}") in session[
            "_flashes"
        ]


def test_quote_result_template_contains_email_self_form(app: Flask) -> None:
    """Render quote results with the self-email form for logged-in users.

    Args:
        app: Test Flask app fixture with database bindings.

    Returns:
        None. Assertions validate the new self-email card and route action.

    External dependencies:
        * Calls ``quotes.lookup_quote`` through
          :meth:`flask.testing.FlaskClient.post` to render ``quote_result.html``.
    """

    customer = User(email="template-self@example.com", role="customer")
    customer.set_password("password123")
    db.session.add(customer)
    db.session.commit()

    quote = _create_quote_for_user(customer)

    client = app.test_client()
    _login_client(client, customer.id)

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Email a Copy to Myself" in html
    assert f"/quotes/{quote.quote_id}/email-self" in html
    assert 'name="return_quote"' not in html
