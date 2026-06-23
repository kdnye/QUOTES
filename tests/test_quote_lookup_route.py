from __future__ import annotations

import json
import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import Quote, User, ZipZone, db


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


def _create_user(*, role: str = "customer", employee_approved: bool = False) -> User:
    """Create and persist a user record for lookup authorization tests.

    Args:
        role: Application role assigned to the created user.
        employee_approved: Employee approval flag used for staff access tests.

    Returns:
        Persisted :class:`app.models.User` instance.

    External dependencies:
        * Uses :data:`app.models.db.session` for persistence.
    """

    user = User(
        email=f"lookup-{uuid.uuid4()}@example.com",
        role=role,
        employee_approved=employee_approved,
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


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

    user = _create_user(role="customer")
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


def test_lookup_quote_get_with_quote_id_renders_result_context(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve a quote directly from a GET query-string quote ID."""

    client = app.test_client()
    user = _create_user_and_login(client)

    quote = Quote(
        quote_id="Q-TYUIOP23",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=120.0,
        total=310.0,
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

    response = client.get(f"/quotes/lookup?quote_id={quote.quote_id}")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"


def test_lookup_quote_get_with_client_reference_renders_result_context(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve a quote from GET query-string client reference lookups."""

    client = app.test_client()
    user = _create_user_and_login(client)

    quote = Quote(
        quote_id="Q-RTYUJK23",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=110.0,
        total=290.0,
        client_reference="ACME-REF-1",
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

    response = client.get(
        "/quotes/lookup?lookup_mode=client_reference&client_reference=acme-ref-1"
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"


def test_lookup_quote_get_client_reference_without_lookup_mode_defaults_correctly(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Infer client-reference mode on GET when only client_reference is provided."""

    client = app.test_client()
    user = _create_user_and_login(client)

    quote = Quote(
        quote_id="Q-ASDFGH23",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=95.0,
        total=250.0,
        client_reference="REF-ONLY-LINK",
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

    response = client.get("/quotes/lookup?client_reference=ref-only-link")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"


def test_lookup_quote_post_invalid_readable_quote_id_renders_lookup_with_flash(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show a validation error when users submit a malformed readable quote ID (Q-XXXXXXXX)."""

    client = app.test_client()
    _create_user_and_login(client)

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )

    response = client.post("/quotes/lookup", data={"quote_id": "not-a-readable-id"})

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=lookup_quote.html"
    with client.session_transaction() as session:
        assert (
            "danger",
            "We could not find a matching quote for that lookup.",
        ) in session["_flashes"]


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
            "We could not find a matching quote for that lookup.",
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


def test_lookup_quote_post_client_reference_returns_controlled_list_for_collisions(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client reference collisions should return the lookup template list view."""

    client = app.test_client()
    user = _create_user_and_login(client)
    quote_a = Quote(
        quote_id="Q-RTYPLM23",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=120.0,
        total=300.0,
        quote_metadata='{"client_reference":"PO 1001"}',
        user_id=user.id,
        user_email=user.email,
    )
    quote_b = Quote(
        quote_id="Q-RTYPLM24",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=120.0,
        total=305.0,
        quote_metadata='{"client_reference":"PO 1001"}',
        user_id=user.id,
        user_email=user.email,
    )
    db.session.add_all([quote_a, quote_b])
    db.session.commit()

    captured: dict[str, object] = {}

    def _fake_render(template_name: str, **context: object) -> str:
        captured["template_name"] = template_name
        captured["context"] = context
        return f"template={template_name}"

    monkeypatch.setattr("app.quotes.routes.render_template", _fake_render)
    response = client.post(
        "/quotes/lookup",
        data={"lookup_mode": "client_reference", "client_reference": "po 1001"},
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=lookup_quote.html"
    assert captured["template_name"] == "lookup_quote.html"
    assert len(captured["context"]["matching_quotes"]) == 2


def test_lookup_quote_post_customer_cannot_access_other_users_quote(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Customers should receive not-found feedback for another user's quote."""

    client = app.test_client()
    current_user_record = _create_user_and_login(client)
    other_user = _create_user(role="customer")

    quote = Quote(
        quote_id="Q-MNBVCXZ2",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=150.0,
        total=325.0,
        quote_metadata="{}",
        user_id=other_user.id,
        user_email=other_user.email,
    )
    db.session.add(quote)
    db.session.commit()

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert current_user_record.id != other_user.id
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=lookup_quote.html"
    with client.session_transaction() as session:
        assert (
            "danger",
            "Quote not found. Please verify the Quote ID and try again.",
        ) in session["_flashes"]


def test_lookup_quote_post_approved_employee_can_access_other_users_quote(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approved employees should have global quote visibility."""

    client = app.test_client()
    employee = _create_user(role="employee", employee_approved=True)
    _login_client(client, employee.id)
    customer = _create_user(role="customer")

    quote = Quote(
        quote_id="Q-QWERTY23",
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=150.0,
        total=325.0,
        quote_metadata="{}",
        user_id=customer.id,
        user_email=customer.email,
    )
    db.session.add(quote)
    db.session.commit()

    monkeypatch.setattr(
        "app.quotes.routes.render_template",
        lambda template_name, **_: f"template={template_name}",
    )
    monkeypatch.setattr("app.quotes.routes.is_quote_email_smtp_enabled", lambda: True)
    monkeypatch.setattr("app.quotes.routes.user_has_mail_privileges", lambda _: True)

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"


# ---------------------------------------------------------------------------
# Full HTML-render tests
#
# The tests below exercise the rendered ``quote_result.html`` template
# end-to-end (no ``render_template`` monkeypatch) so the lookup route's
# response body is asserted against real markup. They migrated from
# tests/test_quote_lookup.py during the test-suite audit; the route file
# already covered every "mocked render context" scenario, so the moves
# below are the unique HTML-shape assertions only.
# ---------------------------------------------------------------------------


def _create_quote(
    user: User,
    quote_id: str,
    *,
    quote_metadata: str,
    total: float = 210.0,
) -> Quote:
    """Persist a quote record with deterministic values for HTML assertions."""

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
    assert "Client Reference" in html


def test_lookup_quote_post_existing_quote_renders_full_html(app: Flask) -> None:
    """Existing quote IDs should render core quote details and the total section."""

    client = app.test_client()
    user = _create_user_and_login(client)
    quote = _create_quote(
        user,
        "Q-HJKMNP23",
        quote_metadata=json.dumps({"accessorial_total": 10.0, "pieces": 3}),
        total=210.0,
    )
    db.session.add_all(
        [
            ZipZone(zipcode="30301", dest_zone=1, notes="Origin dock closes at 4 PM"),
            ZipZone(
                zipcode="60601", dest_zone=2, notes="Destination requires liftgate"
            ),
        ]
    )
    db.session.commit()

    response = client.post("/quotes/lookup", data={"quote_id": quote.quote_id})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Quote Result" in html
    assert "Origin: 30301 | Destination: 60601" in html
    assert html.index("Origin: 30301 | Destination: 60601") < html.index(
        "Shipment Note for 30301:"
    )
    assert "Quote Total" in html
    assert "$210.00" in html
    assert "Pieces: 3" in html
    assert "Shipment Note for 30301:" in html
    assert "Shipment Note for 60601:" in html
    assert "Origin dock closes at 4 PM" in html
    assert "Destination requires liftgate" in html


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


def test_lookup_quote_post_client_reference_returns_single_match(
    app: Flask,
) -> None:
    """Client reference lookup should render result when exactly one scoped match exists."""

    client = app.test_client()
    user = _create_user_and_login(client)
    _create_quote(
        user,
        "Q-NMPQRT23",
        quote_metadata=json.dumps({"client_reference": "PO 10452"}),
    )

    response = client.post(
        "/quotes/lookup",
        data={"lookup_mode": "client_reference", "client_reference": "po 10452"},
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Quote Result" in html
    assert "Q-NMPQRT23" in html


def test_lookup_quote_post_client_reference_collision_shows_controlled_list_html(
    app: Flask,
) -> None:
    """Collisions must not auto-select a first record and should show a list."""

    client = app.test_client()
    user = _create_user_and_login(client)
    _create_quote(
        user,
        "Q-COLLIDE2",
        quote_metadata=json.dumps({"client_reference": "OPS-REF"}),
    )
    _create_quote(
        user,
        "Q-COLLIDE3",
        quote_metadata=json.dumps({"client_reference": "OPS-REF"}),
        total=245.5,
    )

    response = client.post(
        "/quotes/lookup",
        data={"lookup_mode": "client_reference", "client_reference": "OPS-REF"},
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Multiple matches found" in html
    assert "Q-COLLIDE2" in html
    assert "Q-COLLIDE3" in html


def test_lookup_quote_post_client_reference_scope_excludes_other_users(
    app: Flask,
) -> None:
    """Customer users must only see collisions from their own quote scope."""

    client = app.test_client()
    user = _create_user_and_login(client)
    other_user = _create_user(role="customer")
    _create_quote(
        user,
        "Q-OWNMATCH2",
        quote_metadata=json.dumps({"client_reference": "PO-ONLY-MINE"}),
    )
    _create_quote(
        other_user,
        "Q-OTHERMAT2",
        quote_metadata=json.dumps({"client_reference": "PO-ONLY-MINE"}),
    )

    response = client.post(
        "/quotes/lookup",
        data={"lookup_mode": "client_reference", "client_reference": "PO-ONLY-MINE"},
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Quote Result" in html
    assert "Q-OWNMATCH2" in html
    assert "Q-OTHERMAT2" not in html
