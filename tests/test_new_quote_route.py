from __future__ import annotations

import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import FuelSurcharge, User, ZipZone, db
from app.services.settings import set_setting


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
    """Render configured VSC zones, matrix, and last-update values."""

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    set_setting("vsc_zones", '{"NATIONAL":[1,2],"WEST":[3,4]}')
    set_setting("vsc_matrix", '{"A":{"NATIONAL":0.11,"WEST":0.09}}')
    set_setting("vsc_last_update", "2026-04-30T12:00:00Z")
    set_setting("vsc_zones", "{\"1\":\"PADD1\",\"8\":\"PADD5\"}")
    set_setting("vsc_matrix", "[{\"min\":3.5,\"max\":4.0,\"pct\":0.185},{\"min\":4.0,\"max\":4.5,\"pct\":0.21}]")
    db.session.add_all([
        FuelSurcharge(padd_region="NATIONAL", current_rate=3.6),
        FuelSurcharge(padd_region="PADD1", current_rate=3.6),
        FuelSurcharge(padd_region="PADD5", current_rate=4.1),
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
    assert "2026-04-30T12:00:00Z" in zones_html
    assert "2026-04-30T12:00:00Z" in matrix_html
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
    """Render RIA last-update time in Phoenix format plus computed FSC rows.

    Inputs:
        app: Flask application fixture configured for route testing.

    Outputs:
        None. Confirms the page includes converted Phoenix-time text and the
        zone/FSC table rows.

    External dependencies:
        * Calls :func:`app.services.settings.set_setting` to persist
          ``vsc_last_update`` used by the view.
        * Calls :meth:`flask.testing.FlaskClient.get` for
          ``/admin/ria-rates``.
    """

    admin = User(email=f"admin-{uuid.uuid4()}@example.com", role="super_admin")
    admin.set_password("password123")
    db.session.add(admin)
    set_setting("vsc_last_update", "2026-04-30T12:00:00Z")
    set_setting("vsc_zones", "{\"1\":\"PADD1\",\"8\":\"PADD5\"}")
    set_setting("vsc_matrix", "[{\"min\":3.5,\"max\":4.0,\"pct\":0.185},{\"min\":4.0,\"max\":4.5,\"pct\":0.21}]")
    db.session.add_all([
        FuelSurcharge(padd_region="NATIONAL", current_rate=3.6),
        FuelSurcharge(padd_region="PADD1", current_rate=3.6),
        FuelSurcharge(padd_region="PADD5", current_rate=4.1),
    ])
    db.session.commit()

    client = app.test_client()
    _login_client(client, admin.id)
    response = client.get("/admin/ria-rates")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "2026-04-30 05:00:00 AM MST" in html
    assert "<td>1</td>" in html
    assert "<td>PADD1</td>" in html
    assert "<td>18.5%</td>" in html
    assert "<td>8</td>" in html
    assert "<td>PADD5</td>" in html
    assert "<td>21.0%</td>" in html
