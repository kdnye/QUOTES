"""Schema tests for the Science Care multi-lab quote tables."""

from __future__ import annotations

import pytest
from flask import Flask
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCAccessorialMap,
    SCBoxType,
    SCConsumable,
    SCEstablishedLane,
    SCLab,
    SCQuoteSession,
    SCQuoteSessionLeg,
    SCTissueBoxCapacity,
    SCTissueCode,
    User,
    db,
)


class TestSCModelsConfig:
    """Configuration overrides for SC model tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCModelsConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCModelsConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def test_is_sc_admin_defaults_false(app: Flask) -> None:
    user = User(
        email="sc-default@example.com",
        name="SC Default",
        password_hash="x",
    )
    db.session.add(user)
    db.session.commit()
    db.session.refresh(user)
    assert user.is_sc_admin is False


def test_sc_lab_unique_per_rate_set(app: Flask) -> None:
    db.session.add(
        SCLab(lab_code="SCCA", lab_name="Tucson", origin_zip="85705")
    )
    db.session.commit()
    db.session.add(
        SCLab(lab_code="SCCA", lab_name="Tucson dupe", origin_zip="85706")
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    # Same lab_code under a different rate_set is fine.
    db.session.add(
        SCLab(
            lab_code="SCCA",
            lab_name="Other tenant",
            origin_zip="85706",
            rate_set="other_tenant",
        )
    )
    db.session.commit()


def test_sc_rate_set_defaults_to_science_care(app: Flask) -> None:
    lab = SCLab(lab_code="SCAZ", lab_name="Az", origin_zip="85040")
    box = SCBoxType(
        code="MED", label="Medium", length_in=20, width_in=15, height_in=18
    )
    tissue = SCTissueCode(
        tissue_code="PELV03", description="Pelvis", unit_weight_lb=79.0
    )
    db.session.add_all([lab, box, tissue])
    db.session.commit()
    for row in (lab, box, tissue):
        db.session.refresh(row)
        assert row.rate_set == RATE_SET_SCIENCE_CARE


def test_sc_consumable_compound_unique(app: Flask) -> None:
    db.session.add(
        SCConsumable(
            consumable_type="dry_ice",
            temp_mode="frozen",
            scope="domestic",
            weight_lb_per_box=25.0,
        )
    )
    db.session.commit()
    db.session.add(
        SCConsumable(
            consumable_type="dry_ice",
            temp_mode="frozen",
            scope="domestic",
            weight_lb_per_box=25.0,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    # Same type/mode but different scope is allowed.
    db.session.add(
        SCConsumable(
            consumable_type="dry_ice",
            temp_mode="frozen",
            scope="intl",
            weight_lb_per_box=55.0,
        )
    )
    db.session.commit()


def test_sc_established_lane_service_types(app: Flask) -> None:
    db.session.add(
        SCEstablishedLane(
            origin_zip="90808",
            dest_zip="98101",
            service_type="Air",
            rate=250.0,
        )
    )
    db.session.add(
        SCEstablishedLane(
            origin_zip="90808",
            dest_zip="98101",
            service_type="Hotshot",
            rate=350.0,
        )
    )
    db.session.add(
        SCEstablishedLane(
            origin_zip="90808",
            dest_zip="98101",
            service_type="Any",
            rate=300.0,
        )
    )
    db.session.commit()
    rows = SCEstablishedLane.query.filter_by(
        origin_zip="90808", dest_zip="98101"
    ).all()
    assert {r.service_type for r in rows} == {"Air", "Hotshot", "Any"}

    # Duplicate (origin, dest, service_type) under same rate_set fails.
    db.session.add(
        SCEstablishedLane(
            origin_zip="90808",
            dest_zip="98101",
            service_type="Air",
            rate=999.0,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_sc_accessorial_map_unique_per_form_field(app: Flask) -> None:
    db.session.add(
        SCAccessorialMap(
            form_field="J3",
            display_label="4 Hour Window",
            accessorial_name="4hr Window",
        )
    )
    db.session.commit()
    db.session.add(
        SCAccessorialMap(
            form_field="J3",
            display_label="dupe",
            accessorial_name="dupe",
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_sc_quote_session_and_legs_round_trip(app: Flask) -> None:
    user = User(
        email="sc-session@example.com",
        name="SC Session",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(user)
    db.session.commit()

    session = SCQuoteSession(user_id=user.id, grand_total=792.36)
    db.session.add(session)
    db.session.flush()

    leg = SCQuoteSessionLeg(
        session_id=session.id,
        leg_index=1,
        winner_mode="Air",
        winner_total=294.69,
    )
    db.session.add(leg)
    db.session.commit()

    fetched = SCQuoteSession.query.filter_by(user_id=user.id).one()
    assert fetched.grand_total == pytest.approx(792.36)
    legs = SCQuoteSessionLeg.query.filter_by(session_id=fetched.id).all()
    assert len(legs) == 1
    assert legs[0].winner_mode == "Air"

    # Duplicate (session_id, leg_index) is rejected.
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=1,
            winner_mode="Hotshot",
            winner_total=400.0,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_sc_quote_session_leg_consumables_json_round_trip(app: Flask) -> None:
    import json

    user = User(
        email="sc-cons@example.com",
        name="SC Cons",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(user)
    db.session.commit()

    session = SCQuoteSession(user_id=user.id, grand_total=100.0)
    db.session.add(session)
    db.session.flush()

    # consumables_json round-trips JSON intact.
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=1,
            winner_mode="Air",
            winner_total=100.0,
            consumables_json=json.dumps({"1": 2, "3": 5}),
        )
    )
    # NULL consumables_json is still allowed for legs that pre-date
    # the per-leg consumables feature.
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=2,
            winner_mode="Hotshot",
            winner_total=80.0,
        )
    )
    db.session.commit()

    rows = (
        SCQuoteSessionLeg.query.filter_by(session_id=session.id)
        .order_by(SCQuoteSessionLeg.leg_index)
        .all()
    )
    assert json.loads(rows[0].consumables_json) == {"1": 2, "3": 5}
    assert rows[1].consumables_json is None


def test_sc_quote_session_leg_boxes_json_round_trip(app: Flask) -> None:
    import json

    user = User(
        email="sc-boxes@example.com",
        name="SC Boxes",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(user)
    db.session.commit()

    session = SCQuoteSession(user_id=user.id, grand_total=200.0)
    db.session.add(session)
    db.session.flush()

    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=1,
            winner_mode="Air",
            winner_total=200.0,
            boxes_json=json.dumps({"MED": 2, "XLG": 1}),
        )
    )
    # NULL boxes_json is still allowed for legs written before this
    # feature shipped.
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=2,
            winner_mode="Hotshot",
            winner_total=150.0,
        )
    )
    db.session.commit()

    rows = (
        SCQuoteSessionLeg.query.filter_by(session_id=session.id)
        .order_by(SCQuoteSessionLeg.leg_index)
        .all()
    )
    assert json.loads(rows[0].boxes_json) == {"MED": 2, "XLG": 1}
    assert rows[1].boxes_json is None


def test_sc_tissue_box_capacity_unique_per_tissue_box(app: Flask) -> None:
    # Capacity rows are deduped on (rate_set, tissue_code, box_code).
    # The same (tissue, box) pair under a different tenant is allowed
    # so the data partition stays tenant-aligned.
    db.session.add(
        SCTissueBoxCapacity(
            tissue_code="ARM01", box_code="LRG", pieces_per_box=7
        )
    )
    db.session.commit()

    db.session.add(
        SCTissueBoxCapacity(
            tissue_code="ARM01", box_code="LRG", pieces_per_box=99
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    # Same (tissue, box) for another tenant is fine.
    db.session.add(
        SCTissueBoxCapacity(
            tissue_code="ARM01",
            box_code="LRG",
            pieces_per_box=5,
            rate_set="other_tenant",
        )
    )
    db.session.commit()


def test_sc_small_airtray_seeded_by_migration(app: Flask) -> None:
    # The capacity migration inserts SMALL_AIRTRAY into sc_box_types so
    # the per-row dropdown can always offer it. The placeholder
    # dimensions are zero - an SC admin must populate them before the
    # allocator will use the box for quoting.
    box = SCBoxType.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, code="SMALL_AIRTRAY"
    ).one()
    assert box.length_in == 0.0
    assert box.width_in == 0.0
    assert box.height_in == 0.0
    assert box.tare_weight_lb == 0.0
