"""Tests for the /admin/quotes HTML + CSV listing."""

from __future__ import annotations

import json
import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import Quote, User, db


class TestAdminQuotesConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestAdminQuotesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestAdminQuotesConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_employee() -> User:
    user = User(
        email=f"admin-quotes-{uuid.uuid4()}@example.com",
        role="employee",
        employee_approved=True,
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _add_quote(
    user: User,
    *,
    quote_id: str,
    quote_metadata: str,
    client_reference: str | None = None,
    total: float = 250.0,
) -> Quote:
    quote = Quote(
        quote_id=quote_id,
        quote_type="Air",
        origin="85705",
        destination="98101",
        weight=120.0,
        total=total,
        quote_metadata=quote_metadata,
        user_id=user.id,
        user_email=user.email,
        client_reference=client_reference,
    )
    db.session.add(quote)
    db.session.commit()
    return quote


def test_admin_quotes_html_renders_accessorial_badges_and_client_ref(
    app: Flask,
) -> None:
    """The HTML listing must surface each accessorial as a badge with
    its dollar amount and show the client reference column."""

    employee = _make_employee()
    _add_quote(
        employee,
        quote_id="Q-ADM00001",
        quote_metadata=json.dumps(
            {
                "accessorials": {
                    "Liftgate": 50.0,
                    "Inside Delivery": 25.5,
                },
                "accessorial_total": 75.5,
            }
        ),
        client_reference="ACME-PO-9001",
        total=312.5,
    )

    client = app.test_client()
    _login(client, employee.id)
    response = client.get("/admin/quotes")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Client reference column + value.
    assert "Client Ref" in html
    assert "ACME-PO-9001" in html

    # Accessorial names render as badges with their dollar amounts.
    assert "badge" in html
    assert "Liftgate" in html
    assert "Inside Delivery" in html
    assert "$50.00" in html
    assert "$25.50" in html
    # Raw JSON must NOT leak into the rendered cell.
    assert '"accessorials"' not in html
    assert '"accessorial_total"' not in html


def test_admin_quotes_html_handles_missing_or_bad_metadata(app: Flask) -> None:
    """Missing client_reference and bad-JSON metadata must not 500
    the listing — they should render an em-dash + empty cell."""

    employee = _make_employee()
    _add_quote(
        employee,
        quote_id="Q-ADM00002",
        quote_metadata="not-json",
        client_reference=None,
    )

    client = app.test_client()
    _login(client, employee.id)
    response = client.get("/admin/quotes")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The em-dash placeholder appears for both the empty Client Ref
    # cell and the empty Accessorials cell.
    assert "—" in html


def test_admin_quotes_csv_exposes_client_reference_column(app: Flask) -> None:
    employee = _make_employee()
    _add_quote(
        employee,
        quote_id="Q-ADM00003",
        quote_metadata=json.dumps(
            {"accessorials": {"Liftgate": 50.0}, "accessorial_total": 50.0}
        ),
        client_reference="REF-CSV-42",
    )

    client = app.test_client()
    _login(client, employee.id)
    response = client.get("/admin/quotes.csv")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # Header includes Client Reference.
    assert "Client Reference" in body
    # Value lands on the same row as the quote.
    assert "REF-CSV-42" in body
    # Accessorials column shows the human-readable summary, not raw JSON.
    assert "Liftgate: $50.00" in body
    assert '"accessorials"' not in body
