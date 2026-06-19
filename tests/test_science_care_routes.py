"""Route auth + render tests for the Science Care blueprint scaffold.

Covers the PR-B surface: the quote form and HTMX partials, the reference
index landing page, and the htmx tag in base.html.

The orchestration POST and the CSV upload/download endpoints land in
follow-up PRs and are not exercised here.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCLab,
    SCTissueCode,
    User,
    db,
)


class TestSCRoutesConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCRoutesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCRoutesConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_user(
    email: str, rate_set: str, is_sc_admin: bool = False
) -> User:
    user = User(
        email=email,
        full_name=email,
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


def test_sc_quote_renders_for_sc_user(app: Flask) -> None:
    user = _make_user("sc@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # All seven legs render.
    for n in range(1, 8):
        assert f"SHIPMENT {n}" in html
    # HTMX script tag from base.html is present. Use the pinned URL so
    # CodeQL doesn't flag a loose "htmx.org" substring as incomplete URL
    # sanitization (it is a regression assertion, not a security check).
    assert "unpkg.com/htmx.org@1.9.12" in html


def test_sc_quote_blocks_non_sc_user(app: Flask) -> None:
    user = _make_user("non-sc@example.com", rate_set="default")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 403


def test_sc_reference_blocks_plain_sc_user(app: Flask) -> None:
    user = _make_user("plain@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference")
    assert response.status_code == 403


def test_sc_reference_allows_sc_admin(app: Flask) -> None:
    user = _make_user(
        "sc-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # All six reference tables listed.
    for key in (
        "Labs",
        "Tissue codes",
        "Box types",
        "Consumables",
        "Established lanes",
        "Accessorial map",
    ):
        assert key in html


def test_sc_lab_lookup_returns_origin(app: Flask) -> None:
    user = _make_user("lab@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add(
        SCLab(
            lab_code="SCCA",
            lab_name="Tucson",
            origin_zip="85705",
            is_active=True,
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/lab-lookup?leg=1&code=SCCA")
    assert response.status_code == 200
    assert "85705" in response.get_data(as_text=True)


def test_sc_lab_lookup_unknown_code(app: Flask) -> None:
    user = _make_user("lab2@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/lab-lookup?leg=1&code=NOPE")
    assert response.status_code == 200
    assert "No active lab" in response.get_data(as_text=True)


def test_sc_tissue_row_partial_blank(app: Flask) -> None:
    user = _make_user(
        "tissue@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/tissue-row?leg=3&i=4")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="tissue_code_3_4"' in html


def test_sc_tissue_lookup_prefills_known_code(app: Flask) -> None:
    user = _make_user(
        "tissue2@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    db.session.add(
        SCTissueCode(
            tissue_code="PELV03",
            description="Pelvis to Toe",
            unit_weight_lb=79.0,
            default_box_type_code="XL",
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pelvis to Toe" in html
    assert "79.00" in html
    assert "XL" in html


def test_sc_tissue_lookup_unknown_code_preserves_input(app: Flask) -> None:
    user = _make_user(
        "tissue3@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=NOPE"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Bootstrap invalid styling + the typed code preserved.
    assert "is-invalid" in html
    assert 'value="NOPE"' in html
    assert "Unknown tissue code" in html


def test_sc_tissue_lookup_accepts_dynamic_param_name(app: Flask) -> None:
    # HTMX sends the input's `name` (tissue_code_<leg>_<i>) as the query
    # parameter, not `code`. Verify the fallback resolves it.
    user = _make_user(
        "tissue4@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    db.session.add(
        SCTissueCode(
            tissue_code="PELV03",
            description="Pelvis to Toe",
            unit_weight_lb=79.0,
            default_box_type_code="XL",
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=2&i=3&tissue_code_2_3=PELV03"
    )
    assert response.status_code == 200
    assert "Pelvis to Toe" in response.get_data(as_text=True)


def test_sc_lab_lookup_accepts_dynamic_param_name(app: Flask) -> None:
    user = _make_user("lab3@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add(
        SCLab(
            lab_code="SCAZ",
            lab_name="Phoenix",
            origin_zip="85040",
            is_active=True,
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/lab-lookup?leg=5&lab_code_5=SCAZ"
    )
    assert response.status_code == 200
    assert "85040" in response.get_data(as_text=True)


def test_sc_lookup_endpoints_survive_garbage_query_params(app: Flask) -> None:
    user = _make_user(
        "garbage@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    for path in (
        "/sc/quote/lab-lookup?leg=abc",
        "/sc/quote/tissue-row?leg=NaN&i=oops",
        "/sc/quote/tissue-lookup?leg=&i=&code=",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_base_template_loads_htmx() -> None:
    # Path-based check so the regression doesn't depend on rendering a
    # full request - the htmx script tag is mandatory for the SC page to
    # function and must not be accidentally removed.
    with open("templates/base.html", "r", encoding="utf-8") as fp:
        contents = fp.read()
    assert "htmx.org@1.9.12" in contents
