"""Auth gate tests for sc_user_required / sc_admin_required decorators."""

from __future__ import annotations

import pytest
from flask import Blueprint, Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import User, db
from app.policies import sc_admin_required, sc_user_required


class TestSCPoliciesConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCPoliciesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCPoliciesConfig)

    # Mount two probe routes that just return "ok" when allowed.
    probe = Blueprint("sc_probe", __name__)

    @probe.route("/probe/user")
    @sc_user_required
    def probe_user():  # type: ignore[unused-ignore]
        return "ok"

    @probe.route("/probe/admin")
    @sc_admin_required
    def probe_admin():  # type: ignore[unused-ignore]
        return "ok"

    app.register_blueprint(probe)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_user(email: str, rate_set: str, is_sc_admin: bool = False) -> User:
    user = User(
        email=email,
        name=email,
        password_hash="x",
        rate_set=rate_set,
        is_sc_admin=is_sc_admin,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _login(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_sc_user_required_blocks_non_sc(app: Flask) -> None:
    user = _make_user("non-sc@example.com", rate_set="default")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/user")
    assert response.status_code == 403


def test_sc_user_required_allows_science_care_user(app: Flask) -> None:
    user = _make_user("sc-user@example.com", rate_set="science_care")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/user")
    assert response.status_code == 200
    assert response.data == b"ok"


def test_sc_admin_required_blocks_plain_sc_user(app: Flask) -> None:
    user = _make_user(
        "sc-plain@example.com",
        rate_set="science_care",
        is_sc_admin=False,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/admin")
    assert response.status_code == 403


def test_sc_admin_required_allows_sc_admin(app: Flask) -> None:
    user = _make_user(
        "sc-admin@example.com",
        rate_set="science_care",
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/admin")
    assert response.status_code == 200


def test_sc_admin_required_blocks_cross_tenant_sc_admin(app: Flask) -> None:
    # A non-science_care user accidentally flagged is_sc_admin must NOT
    # gain access - strict tenant isolation.
    user = _make_user(
        "wrong-tenant@example.com",
        rate_set="default",
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/admin")
    assert response.status_code == 403


def test_sc_admin_required_allows_fsi_admin(app: Flask) -> None:
    user = _make_user("fsi-admin@example.com", rate_set="default")
    user.is_admin = True
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/probe/admin")
    assert response.status_code == 200


def test_sc_user_required_redirects_anonymous(app: Flask) -> None:
    client = app.test_client()
    response = client.get("/probe/user", follow_redirects=False)
    assert response.status_code in (302, 401)
