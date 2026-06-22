"""Tests for the rate-set → landing-endpoint mapping used after login.

Covers ``app.services.rate_sets.landing_endpoint_for_user`` plus the
related ``sc_user_required`` policy widening so users tagged with the
legacy ``"scicr"`` rate set both redirect to ``/sc/quote`` AND are
allowed through the access gate.
"""

from __future__ import annotations

import pytest
from flask import Blueprint, Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import User, db
from app.policies import sc_user_required
from app.services.rate_sets import (
    DEFAULT_LANDING_ENDPOINT,
    landing_endpoint_for_user,
)


class _StubUser:
    """Lightweight user object exposing only ``rate_set`` for unit tests."""

    def __init__(self, rate_set: str | None) -> None:
        self.rate_set = rate_set


def test_landing_endpoint_defaults_for_unmapped_rate_set() -> None:
    assert (
        landing_endpoint_for_user(_StubUser("default")) == DEFAULT_LANDING_ENDPOINT
    )


def test_landing_endpoint_defaults_when_rate_set_missing() -> None:
    assert landing_endpoint_for_user(_StubUser(None)) == DEFAULT_LANDING_ENDPOINT


def test_landing_endpoint_maps_scicr_to_sc_quote() -> None:
    assert (
        landing_endpoint_for_user(_StubUser("scicr"))
        == "science_care.sc_quote_form"
    )


def test_landing_endpoint_maps_science_care_to_sc_quote() -> None:
    assert (
        landing_endpoint_for_user(_StubUser("science_care"))
        == "science_care.sc_quote_form"
    )


def test_landing_endpoint_normalizes_case_and_whitespace() -> None:
    assert (
        landing_endpoint_for_user(_StubUser("  SCICR "))
        == "science_care.sc_quote_form"
    )


class _PolicyTestConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    _PolicyTestConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(_PolicyTestConfig)

    probe = Blueprint("scicr_probe", __name__)

    @probe.route("/probe/sc-user")
    @sc_user_required
    def probe_user():
        return "ok"

    app.register_blueprint(probe)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_user(email: str, rate_set: str) -> User:
    user = User(email=email, name=email, password_hash="x", rate_set=rate_set)
    db.session.add(user)
    db.session.commit()
    return user


def _login(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_sc_user_required_allows_scicr_rate_set(app: Flask) -> None:
    """scicr-tagged users must be allowed through the SC user gate
    (otherwise the login-redirect to /sc/quote would 403)."""
    user = _make_user("scicr-user@example.com", rate_set="scicr")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/sc-user")
    assert response.status_code == 200
    assert response.data == b"ok"
