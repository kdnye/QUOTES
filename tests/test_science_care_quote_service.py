"""Unit tests for the multi-leg orchestrator service.

``app.services.quote.create_quote`` is monkey-patched so the tests stay
focused on the SC-specific orchestration (allocation, consumable adds,
cheapest-of routing, persistence) and don't need a fully populated
rate-table schema.
"""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask
from werkzeug.datastructures import ImmutableMultiDict

from app import create_app
from app.models import (
    RATE_SET_SCIENCE_CARE,
    Quote,
    SCAccessorialMap,
    SCBoxType,
    SCConsumable,
    SCEstablishedLane,
    SCLab,
    SCQuoteSession,
    SCQuoteSessionLeg,
    SCTissueCode,
    User,
    db,
)
from app.services import science_care_quote as svc


class TestSCQuoteServiceConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCQuoteServiceConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCQuoteServiceConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_user() -> User:
    user = User(
        email="sc-orch@example.com",
        name="SC Orch",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _seed_reference(app: Flask) -> None:
    db.session.add_all(
        [
            SCLab(
                lab_code="SCCA",
                lab_name="Tucson",
                origin_zip="85705",
                is_active=True,
            ),
            SCLab(
                lab_code="SCIL",
                lab_name="Chicago",
                origin_zip="60601",
                is_active=True,
            ),
            SCBoxType(
                code="MED",
                label="Medium",
                length_in=20,
                width_in=15,
                height_in=18,
                tare_weight_lb=4.0,
            ),
            SCBoxType(
                code="XL",
                label="X-Large",
                length_in=52,
                width_in=20,
                height_in=15,
                tare_weight_lb=14.0,
            ),
            SCTissueCode(
                tissue_code="PELV03",
                description="Pelvis to Toe",
                unit_weight_lb=79.0,
                default_box_type_code="XL",
                pieces_per_box=1,
            ),
            SCTissueCode(
                tissue_code="MED01",
                description="Medium part",
                unit_weight_lb=10.0,
                default_box_type_code="MED",
                pieces_per_box=2,
            ),
            SCConsumable(
                consumable_type="dry_ice",
                temp_mode="frozen",
                scope="domestic",
                weight_lb_per_box=25.0,
            ),
            SCConsumable(
                consumable_type="gel_pack",
                temp_mode="rtu",
                scope="domestic",
                weight_lb_per_box=20.0,
            ),
            SCAccessorialMap(
                form_field="J3",
                display_label="4 Hour Window",
                accessorial_name="PickUp 4 Hour Window (e.g 10:00-14:00)",
            ),
            SCAccessorialMap(
                form_field="J8",
                display_label="Liftgate",
                accessorial_name="Liftgate Delivery",
            ),
        ]
    )
    db.session.commit()


def _stub_create_quote(monkeypatch: pytest.MonkeyPatch, prices: dict):
    """Replace ``create_quote`` with a stub that returns canned totals.

    ``prices`` maps ``(quote_type, dest_zip)`` to a float price. The
    stub creates a real :class:`Quote` row so the orchestrator's
    persistence path stays exercised.
    """

    calls: list[dict] = []

    def fake_create_quote(**kwargs: Any):
        calls.append(kwargs)
        total = prices.get(
            (kwargs["quote_type"], kwargs["destination"]), 0.0
        )
        q = Quote(
            user_id=kwargs.get("user_id"),
            user_email=kwargs.get("user_email"),
            quote_type=kwargs["quote_type"],
            origin=kwargs["origin"],
            destination=kwargs["destination"],
            weight=kwargs["weight"],
            pieces=kwargs.get("pieces", 1),
            zone="X",
            total=total,
            quote_metadata="{}",
            quote_source=kwargs.get("quote_source"),
            request_ip=kwargs.get("request_ip"),
            rate_set=kwargs.get("rate_set"),
        )
        db.session.add(q)
        db.session.commit()
        db.session.refresh(q)
        return q, {"total": total, "details": {}}

    monkeypatch.setattr(svc, "create_quote", fake_create_quote)
    return calls


def _form(**overrides) -> ImmutableMultiDict:
    """Convenience: build an ImmutableMultiDict for one populated leg."""

    base = {
        "lab_code_1": "SCCA",
        "dest_zip_1": "98101",
        "routing_type_1": "Outbound",
        "temp_mode_1": "frozen",
        "intl_country_1": "",
        "tissue_code_1_1": "PELV03",
        "qty_1_1": "1",
    }
    base.update(overrides)
    return ImmutableMultiDict(base.items())


def test_cheapest_picks_min_of_three() -> None:
    mode, total = svc._cheapest_for_leg("Outbound", 300, 250, 200)
    assert (mode, total) == ("Established", 200)
    mode, total = svc._cheapest_for_leg("Outbound", 300, 250, None)
    assert (mode, total) == ("Hotshot", 250)
    mode, total = svc._cheapest_for_leg("Outbound", 0, 0, 0)
    assert mode is None and total == 0


def test_sc_to_sc_prefers_established_even_when_higher() -> None:
    mode, total = svc._cheapest_for_leg("SC to SC", 100, 100, 250)
    assert (mode, total) == ("Established", 250)


def test_sc_to_sc_falls_back_when_no_established() -> None:
    mode, total = svc._cheapest_for_leg("SC to SC", 300, 250, None)
    assert (mode, total) == ("Hotshot", 250)


def test_international_leg_skipped(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    calls = _stub_create_quote(monkeypatch, {})
    form = _form(intl_country_1="France")
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    assert result["grand_total"] == 0
    assert result["legs"][0].skip_reason == "international"
    # Confirmed: no API calls fired for the skipped leg.
    assert calls == []


def test_full_orchestration_persists_session_and_picks_winner(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    prices = {
        ("Air", "98101"): 300.0,
        ("Hotshot", "98101"): 250.0,
    }
    calls = _stub_create_quote(monkeypatch, prices)

    form = _form()
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")

    assert result["grand_total"] == 250.0
    leg = result["legs"][0]
    assert leg.winner_mode == "Hotshot"
    assert leg.winner_total == 250.0
    assert leg.air_quote is not None and leg.air_quote.total == 300.0
    assert leg.hotshot_quote is not None
    # Frozen + domestic + 1 box -> 25 lb of dry ice on top of the
    # 79 lb tissue + 14 lb tare = 118 lb total weight.
    assert leg.total_weight_lb == pytest.approx(118.0)

    # 2 calls × 1 leg = 2 stub invocations.
    assert len(calls) == 2

    # Session + leg rows persisted.
    sessions = SCQuoteSession.query.filter_by(user_id=user.id).all()
    assert len(sessions) == 1
    legs = (
        SCQuoteSessionLeg.query.filter_by(session_id=sessions[0].id)
        .order_by(SCQuoteSessionLeg.leg_index)
        .all()
    )
    assert len(legs) == svc.SC_LEG_COUNT
    leg_row = legs[0]
    assert leg_row.winner_mode == "Hotshot"
    assert leg_row.winner_total == 250.0
    assert leg_row.air_quote_id is not None


def test_established_lane_pulled_for_sc_to_sc(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="98101",
            service_type="Any",
            rate=199.0,
        )
    )
    db.session.commit()
    user = _make_user()
    _stub_create_quote(
        monkeypatch,
        {
            ("Air", "98101"): 300.0,
            ("Hotshot", "98101"): 250.0,
        },
    )
    form = _form(routing_type_1="SC to SC")
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    assert leg.established_rate == 199.0
    assert leg.winner_mode == "Established"
    assert leg.winner_total == 199.0


def test_accessorials_resolved_via_map(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    calls = _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    form = _form(acc_J3_1="Y", acc_J8_1="Y")
    svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    air_call = calls[0]
    assert "PickUp 4 Hour Window (e.g 10:00-14:00)" in air_call["accessorials"]
    assert "Liftgate Delivery" in air_call["accessorials"]


def test_established_lane_respects_effective_dates(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date, timedelta

    _seed_reference(app)
    today = date.today()
    # Expired lane (effective_to in the past) - must NOT match.
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="98101",
            service_type="Any",
            rate=99.0,
            effective_to=today - timedelta(days=1),
        )
    )
    # Future lane (effective_from in the future) - must NOT match.
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="98101",
            service_type="Air",
            rate=88.0,
            effective_from=today + timedelta(days=10),
        )
    )
    # Currently-active lane (open-ended bounds) - SHOULD match.
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="98101",
            service_type="Hotshot",
            rate=199.0,
        )
    )
    db.session.commit()
    user = _make_user()
    _stub_create_quote(
        monkeypatch,
        {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0},
    )
    form = _form(routing_type_1="SC to SC")
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    assert leg.established_rate == 199.0
    assert leg.winner_mode == "Established"


def test_unknown_tissue_code_skips_leg(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A typo in any tissue row must NOT silently undercount the leg.
    _seed_reference(app)
    user = _make_user()
    calls = _stub_create_quote(monkeypatch, {})
    form = ImmutableMultiDict(
        {
            "lab_code_1": "SCCA",
            "dest_zip_1": "98101",
            "routing_type_1": "Outbound",
            "temp_mode_1": "frozen",
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "1",
            "tissue_code_1_2": "TYPO",
            "qty_1_2": "1",
        }.items()
    )
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    assert leg.skip_reason is not None
    assert "unknown tissue code" in leg.skip_reason.lower()
    # No quote calls fire when the leg is rejected.
    assert calls == []


def test_quote_source_fits_column(app: Flask) -> None:
    # Quote.quote_source is db.String(20). If QUOTE_SOURCE ever grows
    # past that limit, every SC leg insert will fail on PostgreSQL.
    assert len(svc.QUOTE_SOURCE) <= 20


def test_long_error_truncated_to_skip_reason_column(app: Flask) -> None:
    # SCQuoteSessionLeg.skip_reason is db.String(60). Verify the helper
    # truncates anything longer so the final session commit succeeds.
    long_err = "x" * 500
    out = svc._short_reason(long_err)
    assert out is not None
    assert len(out) <= 60


def test_missing_lab_code_skips_leg(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    calls = _stub_create_quote(monkeypatch, {})
    form = _form(lab_code_1="NOPE")
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    assert result["legs"][0].skip_reason == "missing or unknown lab code"
    assert calls == []


def test_dim_weight_pre_summed_from_boxes(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    calls = _stub_create_quote(
        monkeypatch, {("Air", "98101"): 100.0, ("Hotshot", "98101"): 100.0}
    )
    svc.compute_sc_multileg(
        _form(),
        user,
        request_ip="127.0.0.1",
    )
    # One XL box: 52*20*15 / 166 ≈ 93.97 lb.
    air_call = calls[0]
    assert air_call["dim_weight"] == pytest.approx(
        (52 * 20 * 15) / svc.DIM_DIVISOR
    )
    # length/width/height are NOT passed: create_quote uses our
    # pre-summed dim_weight directly.
    assert "length" not in air_call
    assert "width" not in air_call
    assert "height" not in air_call
