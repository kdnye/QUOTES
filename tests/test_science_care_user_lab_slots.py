"""Per-user default-labs tests for the SC quote form.

Covers the SCUserLabSlot model, the _default_lab_slots helper, the GET
+ POST /sc/quote/defaults routes, and the prefill applied to the main
quote form. Also pins the HTMX out-of-band winner swap on the results
partial and the new tissue-code datalist.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCLab,
    SCTissueCode,
    SCUserLabSlot,
    User,
    db,
)
from app.science_care.routes import _default_lab_slots


class TestSCLabSlotsConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCLabSlotsConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCLabSlotsConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_user(
    email: str = "sc-slots@example.com",
    rate_set: str = RATE_SET_SCIENCE_CARE,
) -> User:
    user = User(
        email=email,
        full_name=email,
        password_hash="x",
        rate_set=rate_set,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _login(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _seed_labs(*codes: str) -> None:
    db.session.add_all(
        [
            SCLab(
                lab_code=c,
                lab_name=c,
                origin_zip="85705",
                is_active=True,
            )
            for c in codes
        ]
    )
    db.session.commit()


# --- model --------------------------------------------------------------


def test_user_lab_slot_unique_per_user_leg(app: Flask) -> None:
    user = _make_user()
    db.session.add(
        SCUserLabSlot(user_id=user.id, leg_index=1, lab_code="SCCA")
    )
    db.session.commit()
    db.session.add(
        SCUserLabSlot(user_id=user.id, leg_index=1, lab_code="SCIL")
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_user_lab_slot_defaults_rate_set(app: Flask) -> None:
    user = _make_user()
    row = SCUserLabSlot(user_id=user.id, leg_index=2, lab_code="SCAZ")
    db.session.add(row)
    db.session.commit()
    db.session.refresh(row)
    assert row.rate_set == RATE_SET_SCIENCE_CARE


# --- helper -------------------------------------------------------------


def test_default_lab_slots_empty_for_new_user(app: Flask) -> None:
    user = _make_user()
    assert _default_lab_slots(user.id) == {}


def test_default_lab_slots_returns_mapping(app: Flask) -> None:
    user = _make_user()
    _seed_labs("SCCA", "SCIL", "SCAZ")
    db.session.add_all(
        [
            SCUserLabSlot(user_id=user.id, leg_index=1, lab_code="SCCA"),
            SCUserLabSlot(user_id=user.id, leg_index=3, lab_code="SCIL"),
        ]
    )
    db.session.commit()
    assert _default_lab_slots(user.id) == {1: "SCCA", 3: "SCIL"}


def test_default_lab_slots_skips_inactive_lab(app: Flask) -> None:
    user = _make_user()
    db.session.add_all(
        [
            SCLab(
                lab_code="SCGONE",
                lab_name="Deactivated",
                origin_zip="85705",
                is_active=False,
            ),
            SCLab(
                lab_code="SCOK",
                lab_name="Active",
                origin_zip="85705",
                is_active=True,
            ),
        ]
    )
    db.session.add_all(
        [
            SCUserLabSlot(
                user_id=user.id, leg_index=1, lab_code="SCGONE"
            ),
            SCUserLabSlot(user_id=user.id, leg_index=2, lab_code="SCOK"),
        ]
    )
    db.session.commit()
    # Deactivated lab is silently dropped from the prefill.
    assert _default_lab_slots(user.id) == {2: "SCOK"}


# --- routes -------------------------------------------------------------


def test_defaults_form_blocks_non_sc_user(app: Flask) -> None:
    user = _make_user("other@example.com", rate_set="default")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/defaults")
    assert response.status_code == 403


def test_defaults_form_renders_for_sc_user(app: Flask) -> None:
    user = _make_user()
    _seed_labs("SCCA", "SCIL")
    db.session.add(
        SCUserLabSlot(user_id=user.id, leg_index=1, lab_code="SCCA")
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/defaults")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Leg 1 prefilled with SCCA, leg 2 blank.
    assert 'name="lab_code_1" value="SCCA"' in html
    assert 'name="lab_code_2" value=""' in html


def test_defaults_post_wipes_then_inserts(app: Flask) -> None:
    user = _make_user()
    _seed_labs("SCCA", "SCIL", "SCAZ")
    db.session.add_all(
        [
            SCUserLabSlot(user_id=user.id, leg_index=1, lab_code="SCCA"),
            SCUserLabSlot(user_id=user.id, leg_index=2, lab_code="SCIL"),
        ]
    )
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/defaults",
        data={
            "lab_code_1": "SCAZ",
            "lab_code_4": "SCIL",
            "lab_code_2": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    rows = (
        SCUserLabSlot.query.filter_by(user_id=user.id)
        .order_by(SCUserLabSlot.leg_index)
        .all()
    )
    assert [(r.leg_index, r.lab_code) for r in rows] == [
        (1, "SCAZ"),
        (4, "SCIL"),
    ]


def test_defaults_post_ignores_unknown_labs(app: Flask) -> None:
    user = _make_user()
    _seed_labs("SCCA")
    client = app.test_client()
    _login(client, user.id)
    client.post(
        "/sc/quote/defaults",
        data={"lab_code_1": "SCCA", "lab_code_2": "GHOST"},
    )
    rows = SCUserLabSlot.query.filter_by(user_id=user.id).all()
    assert [(r.leg_index, r.lab_code) for r in rows] == [(1, "SCCA")]


# --- quote form integration --------------------------------------------


def test_quote_form_prefills_lab_inputs(app: Flask) -> None:
    user = _make_user()
    _seed_labs("SCCA")
    db.session.add(
        SCUserLabSlot(user_id=user.id, leg_index=1, lab_code="SCCA")
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="lab_code_1"' in html
    assert 'value="SCCA"' in html
    # Slot 1 header span now reads SCCA instead of the placeholder.
    assert (
        '<span id="sc-leg-summary-lab-1"'
        in html
    )
    assert ">SCCA<" in html


def test_quote_form_renders_tissue_datalist(app: Flask) -> None:
    user = _make_user()
    db.session.add(
        SCTissueCode(
            tissue_code="PELV03",
            description="Pelvis to Toe",
            unit_weight_lb=79.0,
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    html = client.get("/sc/quote").get_data(as_text=True)
    assert 'id="sc-tissue-codes"' in html
    assert 'value="PELV03"' in html
    # The row input wires to the datalist.
    assert 'list="sc-tissue-codes"' in html


# --- HTMX OOB winner spans ---------------------------------------------


def test_results_partial_emits_oob_winner_spans(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub create_quote so the route doesn't depend on rate tables.
    from app.models import Quote
    from app.services import science_care_quote as svc

    user = _make_user()
    _seed_labs("SCCA")
    db.session.add_all(
        [
            __import__(
                "app.models", fromlist=["SCBoxType"]
            ).SCBoxType(
                code="MED",
                length_in=20,
                width_in=15,
                height_in=18,
                tare_weight_lb=4,
            ),
            SCTissueCode(
                tissue_code="MED01",
                description="Medium part",
                unit_weight_lb=10.0,
                default_box_type_code="MED",
                pieces_per_box=2,
            ),
        ]
    )
    db.session.commit()

    def fake_create_quote(**kwargs):
        q = Quote(
            user_id=kwargs.get("user_id"),
            user_email=kwargs.get("user_email"),
            quote_type=kwargs["quote_type"],
            origin=kwargs["origin"],
            destination=kwargs["destination"],
            weight=kwargs["weight"],
            pieces=kwargs.get("pieces", 1),
            zone="X",
            total=100.0 if kwargs["quote_type"] == "Air" else 80.0,
            quote_metadata="{}",
            rate_set=kwargs.get("rate_set"),
        )
        db.session.add(q)
        db.session.commit()
        db.session.refresh(q)
        return q, {"total": q.total, "details": {}}

    monkeypatch.setattr(svc, "create_quote", fake_create_quote)

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/calculate",
        data={
            "lab_code_1": "SCCA",
            "dest_zip_1": "98101",
            "routing_type_1": "Outbound",
            "temp_mode_1": "frozen",
            "tissue_code_1_1": "MED01",
            "qty_1_1": "2",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Leg 1 has a winner -> "$80.00 Hotshot".
    assert (
        'hx-swap-oob="innerHTML:#sc-leg-summary-winner-1"'
        in html
    )
    assert "$80.00 Hotshot" in html
    # Skipped legs (legs 2-7) emit the "Skipped" marker because they
    # have no lab_code in the form payload.
    assert (
        'hx-swap-oob="innerHTML:#sc-leg-summary-winner-2"'
        in html
    )
    assert "Skipped" in html
