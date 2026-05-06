from __future__ import annotations

from datetime import datetime, timedelta
import uuid

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import Quote, User, db


class TestMyQuotesConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestMyQuotesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestMyQuotesConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _login_client(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _create_user(*, role: str = "customer", employee_approved: bool = False) -> User:
    user = User(
        email=f"quotes-{uuid.uuid4()}@example.com",
        role=role,
        employee_approved=employee_approved,
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def _create_quote(
    user: User, quote_id: str, created_at: datetime, total: float
) -> Quote:
    quote = Quote(
        quote_id=quote_id,
        quote_type="Air",
        origin="10001",
        destination="94105",
        weight=100.0,
        total=total,
        quote_metadata='{"client_reference":"REF-'
        + quote_id[-2:]
        + '","status":"Booked"}',
        user_id=user.id,
        user_email=user.email,
        created_at=created_at,
    )
    db.session.add(quote)
    db.session.commit()
    return quote


def test_my_quotes_customer_sees_only_own_quotes_in_desc_order(app: Flask) -> None:
    client = app.test_client()
    current_user = _create_user(role="customer")
    other_user = _create_user(role="customer")
    _login_client(client, current_user.id)

    now = datetime.utcnow()
    newest = _create_quote(current_user, "Q-AAAAAA22", now, 300.0)
    _create_quote(other_user, "Q-BBBBBB33", now - timedelta(minutes=1), 200.0)
    older = _create_quote(current_user, "Q-CCCCCC44", now - timedelta(minutes=2), 100.0)

    response = client.get("/quotes/my-quotes")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert newest.quote_id in body
    assert older.quote_id in body
    assert "Q-BBBBBB33" not in body
    assert body.index(newest.quote_id) < body.index(older.quote_id)


def test_my_quotes_pagination_uses_page_and_per_page(app: Flask) -> None:
    client = app.test_client()
    current_user = _create_user(role="customer")
    _login_client(client, current_user.id)

    now = datetime.utcnow()
    created_ids = []
    for index in range(3):
        quote_id = f"Q-PPPPPP{index + 2}{index + 2}"
        created_ids.append(quote_id)
        _create_quote(
            current_user,
            quote_id,
            now - timedelta(minutes=index),
            total=50.0 + index,
        )

    response = client.get("/quotes/my-quotes?page=2&per_page=1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert created_ids[0] not in body
    assert created_ids[1] in body
    assert created_ids[2] not in body
