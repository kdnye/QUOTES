from __future__ import annotations

import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import FuelSurcharge, Quote, User, ZipZone, db
from app.services.settings import (
    refresh_settings_cache,
    set_setting,
)


class TestNewQuoteConfig:
    """Configuration overrides for new quote route tests."""

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

    TestNewQuoteConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestNewQuoteConfig)

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
    """Create a customer user and sign them in for quote route requests.

    Args:
        client: Flask test client that should be authenticated.

    Returns:
        Persisted :class:`app.models.User` record used for request ownership.

    External dependencies:
        * Uses :data:`app.models.db.session` for persistence.
        * Calls :func:`_login_client` to establish authenticated session state.
    """

    user = User(email=f"new-quote-{uuid.uuid4()}@example.com", role="customer")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    _login_client(client, user.id)
    return user


def test_new_quote_post_includes_shipment_notes_on_initial_render(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Include ZIP shipment notes when rendering the initial quote result page.

    The route should pass ``origin_notes`` and ``dest_notes`` on the first POST
    that creates a quote, not only during later actions such as emailing.
    """

    client = app.test_client()
    _create_user_and_login(client)
    db.session.add_all(
        [
            ZipZone(zipcode="30301", dest_zone=1, notes="Origin test note"),
            ZipZone(zipcode="60601", dest_zone=2, notes="Destination test note"),
        ]
    )
    db.session.commit()

    captured: dict[str, object] = {}

    def _fake_render(template_name: str, **context: object) -> str:
        captured["template_name"] = template_name
        captured["context"] = context
        return f"template={template_name}"

    monkeypatch.setattr("app.quotes.routes.render_template", _fake_render)
    monkeypatch.setattr(
        "app.quotes.routes.validate_us_zip", lambda *_args, **_kwargs: (True, "")
    )
    monkeypatch.setattr("app.quotes.routes._get_missing_air_rate_tables", lambda: [])
    monkeypatch.setattr(
        "app.quotes.routes.calculate_air_quote",
        lambda *_args, **_kwargs: {"quote_total": 123.45, "miles": 12.0},
    )
    monkeypatch.setattr(
        "app.quotes.routes.check_thresholds", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr("app.quotes.routes.is_quote_email_smtp_enabled", lambda: True)
    monkeypatch.setattr(
        "app.quotes.routes.user_has_mail_privileges", lambda _user: True
    )

    response = client.post(
        "/quotes/new",
        data={
            "quote_type": "Air",
            "origin_zip": "30301",
            "dest_zip": "60601",
            "weight_actual": "10",
            "pieces": "1",
            "length": "0",
            "width": "0",
            "height": "0",
        },
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "template=quote_result.html"
    context = captured["context"]
    assert context["origin_notes"] == "Origin test note"
    assert context["dest_notes"] == "Destination test note"


def test_new_quote_persists_normalized_client_reference(app: Flask) -> None:
    """Persist a trimmed client reference when provided by the request.

    Inputs:
        app: Flask app fixture configured with database-backed quote routes.

    Outputs:
        None. Asserts the saved :class:`app.models.Quote` stores the normalized
        client reference.

    External dependencies:
        Calls :meth:`flask.testing.FlaskClient.post` for ``/quotes/new`` and
        then queries :class:`app.models.Quote` through
        :attr:`app.models.Quote.query`.
    """

    client = app.test_client()
    _create_user_and_login(client)

    response = client.post(
        "/quotes/new",
        json={
            "quote_type": "Air",
            "origin_zip": "30301",
            "dest_zip": "60601",
            "weight_actual": "10",
            "pieces": "1",
            "client_reference": "  REF-123 / A  ",
        },
    )

    assert response.status_code == 200
    quote = Quote.query.order_by(Quote.id.desc()).first()
    assert quote is not None
    assert quote.client_reference == "REF-123 / A"


def test_new_quote_allows_missing_client_reference(app: Flask) -> None:
    """Save quotes without client references as NULL values."""

    client = app.test_client()
    _create_user_and_login(client)

    response = client.post(
        "/quotes/new",
        json={
            "quote_type": "Air",
            "origin_zip": "30301",
            "dest_zip": "60601",
            "weight_actual": "10",
            "pieces": "1",
        },
    )

    assert response.status_code == 200
    quote = Quote.query.order_by(Quote.id.desc()).first()
    assert quote is not None
    assert quote.client_reference is None


@pytest.mark.parametrize(
    "bad_reference",
    [
        "@bad",
        "A" * 65,
    ],
)
def test_new_quote_rejects_invalid_client_reference(
    app: Flask, bad_reference: str
) -> None:
    """Reject invalid client-reference inputs with a 400 response."""

    client = app.test_client()
    _create_user_and_login(client)

    response = client.post(
        "/quotes/new",
        json={
            "quote_type": "Air",
            "origin_zip": "30301",
            "dest_zip": "60601",
            "weight_actual": "10",
            "pieces": "1",
            "client_reference": bad_reference,
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload is not None
    assert "errors" in payload
    assert any("Client reference" in message for message in payload["errors"])


def test_admin_settings_links_include_vsc_pages(app: Flask) -> None:
    """Render links to dedicated VSC settings views on admin settings index."""

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.get("/admin/settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/admin/settings/vsc-zones"' in html
    assert 'href="/admin/settings/vsc-matrix"' in html


def test_admin_vsc_settings_pages_render_payloads(app: Flask) -> None:
    """Render configured VSC zones, matrix, and row-derived pull timestamp."""

    from datetime import datetime

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    set_setting("vsc_zones", '{"NATIONAL":[1,2],"WEST":[3,4]}')
    set_setting("vsc_matrix", '{"A":{"NATIONAL":0.11,"WEST":0.09}}')
    set_setting("vsc_zones", "{\"1\":\"PADD1\",\"8\":\"PADD5\"}")
    set_setting("vsc_matrix", "[{\"min\":3.5,\"max\":4.0,\"pct\":0.185},{\"min\":4.0,\"max\":4.5,\"pct\":0.21}]")
    db.session.add_all([
        FuelSurcharge(
            padd_region="NATIONAL",
            current_rate=3.6,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        ),
        FuelSurcharge(
            padd_region="PADD1",
            current_rate=3.6,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        ),
        FuelSurcharge(
            padd_region="PADD5",
            current_rate=4.1,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        ),
    ])
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)
    zones_response = client.get("/admin/settings/vsc-zones")
    matrix_response = client.get("/admin/settings/vsc-matrix")
    assert zones_response.status_code == 200
    assert matrix_response.status_code == 200
    zones_html = zones_response.get_data(as_text=True)
    matrix_html = matrix_response.get_data(as_text=True)
    assert "2026-06-16 11:00:00 AM MST" in zones_html
    assert "2026-06-16 11:00:00 AM MST" in matrix_html
    assert "NATIONAL" in zones_html
    assert "WEST" in zones_html
    assert "A" in matrix_html


def test_admin_dashboard_links_include_ria_rates_snapshot(app: Flask) -> None:
    """Expose the RIA snapshot page from the admin dashboard quick actions.

    Inputs:
        app: Flask application fixture with in-memory database setup.

    Outputs:
        None. Asserts the dashboard HTML includes a link to the RIA snapshot
        route.

    External dependencies:
        * Calls :meth:`flask.testing.FlaskClient.get` to render
          ``/admin/``.
    """

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.get("/admin/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/admin/ria-rates"' in html


def test_admin_ria_rates_snapshot_renders_phoenix_time_and_zone_fsc(app: Flask) -> None:
    """Render the row-derived pull timestamp plus per-region freshness rows.

    Inputs:
        app: Flask application fixture configured for route testing.

    Outputs:
        None. Confirms the page includes the Phoenix-formatted
        ``MAX(fuel_surcharges.last_updated)`` text, per-region diesel prices,
        per-region last-updated timestamps, and computed FSC percentages.
    """

    from datetime import datetime

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    set_setting("vsc_zones", "{\"1\":\"PADD1\",\"8\":\"PADD5\"}")
    set_setting("vsc_matrix", "[{\"min\":3.5,\"max\":4.0,\"pct\":0.185},{\"min\":4.0,\"max\":4.5,\"pct\":0.21}]")
    db.session.add_all([
        FuelSurcharge(
            padd_region="NATIONAL",
            current_rate=3.6,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        ),
        FuelSurcharge(
            padd_region="PADD1",
            current_rate=3.6,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        ),
        FuelSurcharge(
            padd_region="PADD5",
            current_rate=4.1,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        ),
    ])
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.get("/admin/ria-rates")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "2026-06-16 11:00:00 AM MST" in html
    assert "<td>1</td>" in html
    assert "<td>PADD1</td>" in html
    assert "<td>18.5%</td>" in html
    assert "<td>$3.600</td>" in html
    assert "<td>8</td>" in html
    assert "<td>PADD5</td>" in html
    assert "<td>$4.100</td>" in html
    assert "<td>21.0%</td>" in html
    # No sentinel card on the page anymore.
    assert "Sync Sentinel" not in html


def test_admin_ria_rates_snapshot_ignores_stale_settings_cache(
    app: Flask,
) -> None:
    """The view must reflect rate-row writes made by an out-of-process job.

    The ``sync-eia-rates`` Cloud Run job upserts ``fuel_surcharges`` rows in a
    separate process. The web service's module-level settings cache cannot
    see those writes, but the snapshot must — because it queries the
    ``fuel_surcharges`` table directly.

    Inputs:
        app: Flask application fixture configured for route testing.

    Outputs:
        None. Confirms the page shows the fresh row-derived timestamp even
        though the in-process settings cache was primed with older values.
    """

    from datetime import datetime

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    # Prime the cache with stale settings.
    set_setting("vsc_zones", "{\"1\":\"PADD1\"}")
    set_setting("vsc_matrix", "[{\"min\":3.5,\"max\":4.0,\"pct\":0.185}]")
    db.session.add(
        FuelSurcharge(
            padd_region="PADD1",
            current_rate=3.6,
            last_updated=datetime(2026, 6, 16, 18, 0, 0),
        )
    )
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.get("/admin/ria-rates")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "2026-06-16 11:00:00 AM MST" in html


def test_admin_ria_rates_snapshot_handles_empty_fuel_surcharges(
    app: Flask,
) -> None:
    """Surface a clear message when no rate rows exist yet.

    Inputs:
        app: Flask application fixture configured for route testing.

    Outputs:
        None. Confirms the page renders without a timestamp and includes the
        empty-state guidance.
    """

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    set_setting("vsc_zones", "{\"1\":\"PADD1\"}")
    set_setting("vsc_matrix", "[{\"min\":3.5,\"max\":4.0,\"pct\":0.185}]")
    db.session.commit()
    refresh_settings_cache()

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.get("/admin/ria-rates")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "sync job has not persisted any rates yet" in html


def test_new_quote_get_with_from_quote_prefills_form(app: Flask) -> None:
    """GET ``/quotes/new?from_quote=<id>`` prefills inputs from a prior quote.

    Confirms the prefill plumbing reaches the rendered HTML for every field
    the helper exposes (ZIPs, weight, dims, pieces, accessorial checkboxes,
    client reference, and the quote_type radio).
    """

    import json as _json
    from app.models import Quote

    client = app.test_client()
    user = _create_user_and_login(client)

    source = Quote(
        user_id=user.id,
        user_email=user.email,
        quote_type="Hotshot",
        origin="30301",
        destination="60601",
        weight=250.0,
        actual_weight=250.0,
        dim_weight=0.0,
        pieces=3,
        length=10.0,
        width=12.0,
        height=18.0,
        total=500.0,
        zone="X",
        client_reference="REF-EDIT-1",
        quote_metadata=_json.dumps(
            {"accessorials": {"Liftgate": 75.0, "Residential": 20.0}}
        ),
    )
    db.session.add(source)
    db.session.commit()
    db.session.refresh(source)

    response = client.get(f"/quotes/new?from_quote={source.quote_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # ZIPs + weight + pieces + one dim
    assert 'value="30301"' in html
    assert 'value="60601"' in html
    assert 'value="250.0"' in html
    assert 'name="pieces"' in html and 'value="3"' in html
    assert 'name="length"' in html and 'value="10.0"' in html
    # Hotshot radio is the prefilled quote_type. The template renders
    # Hotshot's <input ... value="Hotshot" checked> because quote_type
    # is set from prefill.
    assert 'value="Hotshot"' in html
    # Reference prefilled so the user can copy/tweak it without retyping.
    assert "REF-EDIT-1" in html


def test_new_quote_get_with_from_quote_scopes_to_user_visibility(
    app: Flask,
) -> None:
    """A customer cannot prefill another user's quote — silently skipped."""

    from app.models import Quote

    client = app.test_client()
    user = _create_user_and_login(client)

    other = User(email="other@example.com", role="customer")
    other.set_password("password123")
    db.session.add(other)
    db.session.commit()

    other_quote = Quote(
        user_id=other.id,
        user_email=other.email,
        quote_type="Air",
        origin="11111",
        destination="22222",
        weight=99.0,
        actual_weight=99.0,
        pieces=1,
        total=10.0,
        zone="X",
    )
    db.session.add(other_quote)
    db.session.commit()
    db.session.refresh(other_quote)

    response = client.get(f"/quotes/new?from_quote={other_quote.quote_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The other user's ZIPs MUST NOT leak into our form.
    assert "11111" not in html
    assert "22222" not in html


def test_edit_quote_dispatches_quote_id_to_new_quote(app: Flask) -> None:
    """POST ``/quotes/edit`` with a Quote ID redirects to ``/quotes/new``."""

    from app.models import Quote

    client = app.test_client()
    user = _create_user_and_login(client)
    source = Quote(
        user_id=user.id,
        user_email=user.email,
        quote_type="Air",
        origin="30301",
        destination="60601",
        weight=10.0,
        actual_weight=10.0,
        pieces=1,
        total=100.0,
        zone="X",
    )
    db.session.add(source)
    db.session.commit()
    db.session.refresh(source)

    response = client.post(
        "/quotes/edit", data={"quote_id": source.quote_id}
    )
    assert response.status_code == 302
    assert (
        f"/quotes/new?from_quote={source.quote_id}" in response.headers["Location"]
    )


def test_edit_quote_dispatches_client_reference_to_new_quote(app: Flask) -> None:
    """Client Reference falls through to single-quote lookup for non-SC users."""

    from app.models import Quote

    client = app.test_client()
    user = _create_user_and_login(client)
    source = Quote(
        user_id=user.id,
        user_email=user.email,
        quote_type="Air",
        origin="30301",
        destination="60601",
        weight=10.0,
        actual_weight=10.0,
        pieces=1,
        total=100.0,
        zone="X",
        client_reference="PO-EDIT-1",
    )
    db.session.add(source)
    db.session.commit()
    db.session.refresh(source)

    response = client.post(
        "/quotes/edit", data={"client_reference": "PO-EDIT-1"}
    )
    assert response.status_code == 302
    assert (
        f"/quotes/new?from_quote={source.quote_id}" in response.headers["Location"]
    )


def test_edit_quote_returns_page_when_lookup_misses(app: Flask) -> None:
    """An unknown reference re-renders the search page with a warning."""

    client = app.test_client()
    _create_user_and_login(client)

    response = client.post(
        "/quotes/edit", data={"quote_id": "Q-BCDFGHJ2"}
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No quote found" in html
