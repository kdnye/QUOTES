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
    SCTissueBoxCapacity,
    SCTissueCode,
    User,
    db,
)
from app.services import science_care_quote as svc
from app.services.science_care_quote import (
    TissueRow,
    allocate_boxes,
    recommended_box_for_qty,
)


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
    # No consumables entered -> 79 lb tissue + 14 lb tare = 93 lb.
    # Consumables only contribute when the user types a Qty.
    assert leg.total_weight_lb == pytest.approx(93.0)

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


def test_established_lane_city_state_fallback(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lane row with no exact dest_zip but matching dest_city/dest_state
    should still apply when the leg's dest_zip resolves to that city/state
    via the ZIP→city helper.

    Mirrors the workbook's lab + "City,State" VLOOKUP: a lane priced for
    "SCPA → Mahwah, NJ" applies to any Mahwah ZIP, not just one
    representative one.
    """

    from app.services import zip_city_lookup

    _seed_reference(app)
    # Lane keyed by metro, NOT by ZIP. dest_zip is required by the schema
    # so use a sentinel ZIP that does NOT match the leg's dest_zip - the
    # match must come from the city/state columns.
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="00000",
            dest_city="Mahwah",
            dest_state="NJ",
            service_type="Any",
            rate=825.0,
        )
    )
    db.session.commit()

    # Stub the ZIP→city helper instead of depending on the production
    # Zipcode_Zones.csv being present in the test environment.
    monkeypatch.setattr(
        zip_city_lookup,
        "lookup_city_state",
        lambda zip_code: ("MAHWAH", "NJ") if zip_code == "07495" else None,
    )
    # _lookup_established imports the helper by name, so patch both
    # bindings to be safe.
    monkeypatch.setattr(svc, "lookup_city_state", lambda z: ("MAHWAH", "NJ") if z == "07495" else None)

    _stub_create_quote(
        monkeypatch,
        {("Air", "07495"): 1200.0, ("Hotshot", "07495"): 1100.0},
    )
    form = _form(dest_zip_1="07495")
    result = svc.compute_sc_multileg(form, user=_make_user(), request_ip="127.0.0.1")
    leg = result["legs"][0]
    assert leg.established_rate == 825.0
    # Established is cheaper than both Air (1200) and Hotshot (1100), so
    # it wins the cheapest-of-three rollup automatically.
    assert leg.winner_mode == "Established"
    assert leg.winner_total == 825.0


def test_established_lane_zip_match_wins_over_city_fallback(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both a ZIP-keyed lane AND a metro-keyed lane exist for the same
    origin, the exact ZIP match must win — even if the metro row is cheaper.

    The ZIP match represents the admin's specific intent (this exact lane,
    this exact price); the metro fallback is a safety net for "any ZIP in
    this city". Letting the cheaper metro row override the explicit ZIP
    row would be surprising.
    """

    from app.services import zip_city_lookup

    _seed_reference(app)
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="07495",
            service_type="Any",
            rate=900.0,
        )
    )
    db.session.add(
        SCEstablishedLane(
            origin_zip="85705",
            dest_zip="00000",
            dest_city="Mahwah",
            dest_state="NJ",
            service_type="Any",
            rate=500.0,
        )
    )
    db.session.commit()

    monkeypatch.setattr(
        zip_city_lookup,
        "lookup_city_state",
        lambda zip_code: ("MAHWAH", "NJ"),
    )
    monkeypatch.setattr(svc, "lookup_city_state", lambda z: ("MAHWAH", "NJ"))

    _stub_create_quote(
        monkeypatch,
        {("Air", "07495"): 2000.0, ("Hotshot", "07495"): 2000.0},
    )
    form = _form(dest_zip_1="07495", routing_type_1="SC to SC")
    result = svc.compute_sc_multileg(form, user=_make_user(), request_ip="127.0.0.1")
    leg = result["legs"][0]
    # Exact ZIP wins (900) - cheaper metro row (500) is ignored.
    assert leg.established_rate == 900.0


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


def test_consumable_picks_drive_weight(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The user's explicit per-consumable Qty should drive the leg's
    # consumable weight - not the temp_mode/scope auto formula.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )

    # Look up the seeded "dry_ice/frozen/domestic" row's id.
    from app.models import RATE_SET_SCIENCE_CARE, SCConsumable

    dry_ice = SCConsumable.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE,
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
    ).one()

    form = _form(**{f"cons_qty_1_{dry_ice.id}": "2"})
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    # 79 lb tissue + 14 lb tare + (2 * 25 lb dry ice) = 143 lb.
    assert leg.total_weight_lb == pytest.approx(143.0)
    assert leg.consumable_picks == {dry_ice.id: 2}


def test_blank_picks_contribute_zero_consumable_weight(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Consumables are opt-in: a leg with every Qty blank contributes
    # 0 lb of consumables. Tissue + tare make up the full leg weight.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    result = svc.compute_sc_multileg(
        _form(), user, request_ip="127.0.0.1"
    )
    leg = result["legs"][0]
    # 79 lb tissue + 14 lb XL tare = 93 lb. No auto consumable add.
    assert leg.total_weight_lb == pytest.approx(93.0)
    assert leg.consumable_weight_lb == 0.0
    assert leg.consumable_picks == {}


def test_consumable_picks_persisted_on_leg(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )

    from app.models import (
        RATE_SET_SCIENCE_CARE,
        SCConsumable,
        SCQuoteSession,
        SCQuoteSessionLeg,
    )

    gel_pack = SCConsumable.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE,
        consumable_type="gel_pack",
        temp_mode="rtu",
        scope="domestic",
    ).one()
    form = _form(
        temp_mode_1="rtu",
        **{f"cons_qty_1_{gel_pack.id}": "3"},
    )
    svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")

    session = SCQuoteSession.query.filter_by(user_id=user.id).one()
    leg_row = (
        SCQuoteSessionLeg.query.filter_by(
            session_id=session.id, leg_index=1
        ).one()
    )
    assert leg_row.consumables_json is not None
    decoded = json.loads(leg_row.consumables_json)
    assert decoded == {str(gel_pack.id): 3}


def test_box_overrides_drive_counts(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The user's explicit per-box-type Count should replace the auto
    # allocation from tissue rows. Tissue weight and the override-only
    # tare/dim totals are what land in the leg.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )

    from app.models import RATE_SET_SCIENCE_CARE, SCBoxType

    med_box = SCBoxType.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, code="MED"
    ).one()

    # Default form puts 1 PELV03 (auto-picks 1 XL box). Override with
    # 3 Mediums and the leg's boxes_by_type must reflect the override
    # only.
    form = _form(**{f"box_count_1_{med_box.id}": "3"})
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    assert leg.box_counts == {"MED": 3}
    # 79 lb tissue + 3 * 4 lb tare = 91 lb. No consumables entered.
    assert leg.total_weight_lb == pytest.approx(91.0)


def test_blank_box_overrides_fall_back_to_auto(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a leg submitted without any box_count_* keys must
    # continue to receive today's tissue-driven auto allocation.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    result = svc.compute_sc_multileg(
        _form(), user, request_ip="127.0.0.1"
    )
    leg = result["legs"][0]
    # Same as test_full_orchestration_persists_session_and_picks_winner.
    assert leg.total_weight_lb == pytest.approx(93.0)
    # box_counts surfaces the auto allocation (1 XL from PELV03 default).
    assert leg.box_counts == {"XL": 1}


def test_box_overrides_persisted_on_leg(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )

    from app.models import (
        RATE_SET_SCIENCE_CARE,
        SCBoxType,
        SCQuoteSession,
        SCQuoteSessionLeg,
    )

    med_box = SCBoxType.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, code="MED"
    ).one()
    form = _form(**{f"box_count_1_{med_box.id}": "2"})
    svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")

    session = SCQuoteSession.query.filter_by(user_id=user.id).one()
    leg_row = (
        SCQuoteSessionLeg.query.filter_by(
            session_id=session.id, leg_index=1
        ).one()
    )
    assert leg_row.boxes_json is not None
    assert json.loads(leg_row.boxes_json) == {"MED": 2}


# --- Per-tissue box capacity (refined allocator) -----------------------------
#
# These tests exercise the new SCTissueBoxCapacity-driven allocator: a tissue
# is shipped using the box that minimises ceil(qty / pieces_per_box), ties
# broken by smaller interior volume. The per-row Box dropdown override is
# honoured when the user picks a specific code.


def _box(**kwargs: Any) -> SCBoxType:
    """Build an unsaved SCBoxType for the pure-Python allocator tests."""

    defaults = dict(
        rate_set=RATE_SET_SCIENCE_CARE,
        length_in=0.0,
        width_in=0.0,
        height_in=0.0,
        tare_weight_lb=0.0,
    )
    defaults.update(kwargs)
    return SCBoxType(**defaults)


def test_recommended_box_picks_smallest_box_count() -> None:
    # ARM01-like: LRG holds 7, XLG holds 10. For qty 8 the XLG wins
    # (1 box vs 2). For qty 7 they tie at 1 box each, so the smaller
    # interior wins (LRG: 32*18*20=11520 < XLG: 52*20*15=15600).
    box_index = {
        "LRG": _box(code="LRG", length_in=32, width_in=18, height_in=20),
        "XLG": _box(code="XLG", length_in=52, width_in=20, height_in=15),
    }
    caps = {"LRG": 7, "XLG": 10}
    assert recommended_box_for_qty(8, caps, box_index) == ("XLG", 10)
    assert recommended_box_for_qty(7, caps, box_index) == ("LRG", 7)
    assert recommended_box_for_qty(1, caps, box_index) == ("LRG", 7)


def test_recommended_box_skips_zero_volume() -> None:
    # SMALL_AIRTRAY placeholder has zero dimensions until an SC admin
    # populates real ones. The allocator must skip it so the freight
    # weight isn't silently undercounted by a zero dim-weight box.
    box_index = {
        "SMALL_AIRTRAY": _box(
            code="SMALL_AIRTRAY", length_in=0, width_in=0, height_in=0
        ),
        "AIRTRAY": _box(
            code="AIRTRAY", length_in=79, width_in=24, height_in=15
        ),
    }
    caps = {"SMALL_AIRTRAY": 1, "AIRTRAY": 1}
    assert recommended_box_for_qty(1, caps, box_index) == ("AIRTRAY", 1)


def test_allocate_boxes_uses_capacity_table_over_legacy_default() -> None:
    # Tissue has a capacity row pointing at LRG and a legacy default of
    # XLG. The new allocator must pick LRG because the capacity table is
    # the source of truth.
    box_index = {
        "LRG": _box(
            code="LRG", length_in=32, width_in=18, height_in=20,
            tare_weight_lb=8.0,
        ),
        "XLG": _box(
            code="XLG", length_in=52, width_in=20, height_in=15,
            tare_weight_lb=14.0,
        ),
    }
    tissue_index = {
        "TST01": SCTissueCode(
            tissue_code="TST01",
            unit_weight_lb=4.0,
            default_box_type_code="XLG",
            pieces_per_box=10,
        ),
    }
    capacity_index = {"TST01": {"LRG": 7, "XLG": 10}}
    rows = [TissueRow(tissue_code="TST01", qty=7)]
    weight, count, by_type, dim, unknown = allocate_boxes(
        rows, tissue_index, box_index, capacity_index=capacity_index
    )
    # 7 pieces of 4 lb = 28 lb tissue + 1 LRG (8 lb tare) = 36 lb.
    assert weight == pytest.approx(36.0)
    assert count == 1
    assert by_type == {"LRG": 1}
    assert unknown == []


def test_allocate_boxes_honours_user_box_pick() -> None:
    # User dropdown override: tissue's recommended box is LRG but the
    # user explicitly selected XLG. The allocator must use the user's
    # pick (as long as the capacity table allows it).
    box_index = {
        "LRG": _box(
            code="LRG", length_in=32, width_in=18, height_in=20,
            tare_weight_lb=8.0,
        ),
        "XLG": _box(
            code="XLG", length_in=52, width_in=20, height_in=15,
            tare_weight_lb=14.0,
        ),
    }
    tissue_index = {
        "TST01": SCTissueCode(
            tissue_code="TST01", unit_weight_lb=4.0,
            default_box_type_code="LRG", pieces_per_box=7,
        ),
    }
    capacity_index = {"TST01": {"LRG": 7, "XLG": 10}}
    rows = [
        TissueRow(tissue_code="TST01", qty=7, user_box_code="XLG"),
    ]
    weight, count, by_type, dim, unknown = allocate_boxes(
        rows, tissue_index, box_index, capacity_index=capacity_index
    )
    assert by_type == {"XLG": 1}
    # 28 lb tissue + 14 lb XLG tare = 42 lb.
    assert weight == pytest.approx(42.0)


def test_allocate_boxes_ignores_invalid_user_box_pick() -> None:
    # The user dropdown override only wins when the capacity table
    # has a non-zero entry for that box. Picking an unallowed box
    # falls back to the recommendation so the leg still produces a
    # quote rather than dropping to zero boxes.
    box_index = {
        "LRG": _box(
            code="LRG", length_in=32, width_in=18, height_in=20,
            tare_weight_lb=8.0,
        ),
        "XLG": _box(
            code="XLG", length_in=52, width_in=20, height_in=15,
            tare_weight_lb=14.0,
        ),
    }
    tissue_index = {
        "TST01": SCTissueCode(
            tissue_code="TST01", unit_weight_lb=4.0,
        ),
    }
    capacity_index = {"TST01": {"LRG": 7}}
    rows = [TissueRow(tissue_code="TST01", qty=7, user_box_code="XLG")]
    _, _, by_type, _, _ = allocate_boxes(
        rows, tissue_index, box_index, capacity_index=capacity_index
    )
    assert by_type == {"LRG": 1}


def test_allocate_boxes_legacy_fallback_when_no_capacities() -> None:
    # Tenants who haven't reloaded their CSV still have only the legacy
    # default_box_type_code + pieces_per_box on SCTissueCode. The
    # allocator must keep producing quotes for them until they migrate.
    box_index = {
        "XL": _box(
            code="XL", length_in=52, width_in=20, height_in=15,
            tare_weight_lb=14.0,
        ),
    }
    tissue_index = {
        "PELV03": SCTissueCode(
            tissue_code="PELV03", unit_weight_lb=79.0,
            default_box_type_code="XL", pieces_per_box=1,
        ),
    }
    rows = [TissueRow(tissue_code="PELV03", qty=1)]
    weight, count, by_type, _, _ = allocate_boxes(
        rows, tissue_index, box_index
    )
    assert by_type == {"XL": 1}
    # 79 lb tissue + 14 lb tare = 93 lb (consumables added by caller).
    assert weight == pytest.approx(93.0)


def test_capacity_driven_orchestration(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # End-to-end: capacity rows for the seeded tissue codes drive the
    # orchestrator's box pick, overriding the legacy
    # default_box_type_code on SCTissueCode.
    _seed_reference(app)
    db.session.add_all(
        [
            SCTissueBoxCapacity(
                tissue_code="PELV03", box_code="MED", pieces_per_box=1
            ),
        ]
    )
    db.session.commit()
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    result = svc.compute_sc_multileg(
        _form(), user, request_ip="127.0.0.1"
    )
    leg = result["legs"][0]
    # Capacity table now ships PELV03 in a Medium box instead of XL.
    # 79 lb tissue + 4 lb MED tare = 83 lb. No consumables entered.
    assert leg.box_counts == {"MED": 1}
    assert leg.total_weight_lb == pytest.approx(83.0)


def test_per_row_box_choice_override_from_form(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The user can pick a non-recommended box from the per-row dropdown
    # (box_choice_<leg>_<i>) and the allocator must honour it. Here we
    # seed both MED and XL capacities for PELV03, set the recommendation
    # to MED via capacity table, and then override via the form.
    _seed_reference(app)
    db.session.add_all(
        [
            SCTissueBoxCapacity(
                tissue_code="PELV03", box_code="MED", pieces_per_box=1
            ),
            SCTissueBoxCapacity(
                tissue_code="PELV03", box_code="XL", pieces_per_box=2
            ),
        ]
    )
    db.session.commit()
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    form = _form(box_choice_1_1="XL")
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    # User forces XL even though MED would also fit a single PELV03.
    assert leg.box_counts == {"XL": 1}
    # 79 lb tissue + 14 lb XL tare = 93 lb. No consumables entered.
    assert leg.total_weight_lb == pytest.approx(93.0)


# --- Weight breakdown (tissue / consumables / box tare) ----------------------


def test_leg_result_carries_weight_breakdown(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every successful leg surfaces tissue / consumables / box tare
    # separately so the results card can show the breakdown. The three
    # numbers must sum to total_weight_lb.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    result = svc.compute_sc_multileg(
        _form(), user, request_ip="127.0.0.1"
    )
    leg = result["legs"][0]
    # PELV03 qty 1 -> 79 lb tissue, 1 XL box (14 lb tare), no
    # consumables entered, 93 lb total.
    assert leg.tissue_weight_lb == pytest.approx(79.0)
    assert leg.box_tare_weight_lb == pytest.approx(14.0)
    assert leg.consumable_weight_lb == 0.0
    assert leg.total_weight_lb == pytest.approx(
        leg.tissue_weight_lb
        + leg.box_tare_weight_lb
        + leg.consumable_weight_lb
    )


def test_weight_breakdown_zero_for_skipped_leg(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A leg without tissue rows is skipped - the breakdown fields stay
    # at zero so the results card can't accidentally double-count them
    # into the grand-total row.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(monkeypatch, {})
    form = ImmutableMultiDict(
        {
            "lab_code_1": "SCCA",
            "dest_zip_1": "98101",
            "routing_type_1": "Outbound",
            "temp_mode_1": "frozen",
            # No tissue_code_1_1 / qty_1_1 - leg is skipped.
        }.items()
    )
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    assert leg.skip_reason == "no tissue rows"
    assert leg.tissue_weight_lb == 0.0
    assert leg.consumable_weight_lb == 0.0
    assert leg.box_tare_weight_lb == 0.0
    assert leg.total_weight_lb == 0.0


def test_weight_breakdown_with_box_override(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A leg-level box-count override (Boxes section) replaces the
    # auto allocation. The breakdown's tare-weight column must reflect
    # the override, not the auto picks - otherwise the three columns
    # wouldn't sum to total_weight_lb.
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )

    from app.models import RATE_SET_SCIENCE_CARE, SCBoxType

    med_box = SCBoxType.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, code="MED"
    ).one()

    form = _form(**{f"box_count_1_{med_box.id}": "3"})
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    leg = result["legs"][0]
    # Override 3 MED boxes (3 × 4 lb tare = 12 lb); tissue 79 lb; no
    # consumables entered. 79 + 12 = 91 lb.
    assert leg.tissue_weight_lb == pytest.approx(79.0)
    assert leg.box_tare_weight_lb == pytest.approx(12.0)
    assert leg.consumable_weight_lb == 0.0
    assert leg.total_weight_lb == pytest.approx(91.0)


# --- compute_leg_subtotals (live HTMX subtotals helper) ---------------------


def test_compute_leg_subtotals_uses_picks_when_present() -> None:
    # User-entered Qty drives the consumable subtotal. The helper must
    # match the orchestrator's opt-in rule (only entered picks count).
    from werkzeug.datastructures import ImmutableMultiDict

    rows = [
        TissueRow(tissue_code="ARM01", qty=2, unit_weight_lb=12.0),
    ]
    box_index = {
        "LRG": _box(
            code="LRG", length_in=32, width_in=18, height_in=20,
            tare_weight_lb=8.0,
        ),
    }
    cons = SCConsumable(
        id=1,
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    form = ImmutableMultiDict(
        {"temp_mode_1": "frozen", "cons_qty_1_1": "3"}.items()
    )
    subtotals = svc.compute_leg_subtotals(
        form,
        leg=1,
        tissue_rows=rows,
        boxes_by_type={"LRG": 1},
        box_index=box_index,
        consumable_index=[cons],
    )
    assert subtotals == pytest.approx({
        "tissue_lb": 24.0,    # 2 × 12 lb
        "consumable_lb": 75.0,  # picks: 3 × 25 lb
        "box_tare_lb": 8.0,   # 1 LRG × 8 lb
        "total_lb": 107.0,
    })


def test_compute_leg_subtotals_zero_consumables_when_no_picks() -> None:
    # Blank consumable Qty contributes 0 lb (opt-in behaviour). The
    # helper must match the orchestrator - no auto fallback.
    from werkzeug.datastructures import ImmutableMultiDict

    rows = [
        TissueRow(tissue_code="ARM01", qty=2, unit_weight_lb=12.0),
    ]
    box_index = {
        "LRG": _box(
            code="LRG", length_in=32, width_in=18, height_in=20,
            tare_weight_lb=8.0,
        ),
    }
    cons = SCConsumable(
        id=1,
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    form = ImmutableMultiDict({"temp_mode_1": "frozen"}.items())
    subtotals = svc.compute_leg_subtotals(
        form,
        leg=1,
        tissue_rows=rows,
        boxes_by_type={"LRG": 2},
        box_index=box_index,
        consumable_index=[cons],
    )
    # No cons_qty_* in the form -> 0 lb consumables.
    assert subtotals == pytest.approx({
        "tissue_lb": 24.0,
        "consumable_lb": 0.0,
        "box_tare_lb": 16.0,
        "total_lb": 40.0,
    })


# --- multi_reference assignment + stamping ----------------------------------


class TestMultiReferenceNormalization:
    """Pure helper tests - no DB / app context needed."""

    def test_blank_input_returns_none(self) -> None:
        assert svc._normalize_multi_reference("") == (None, None)
        assert svc._normalize_multi_reference("   ") == (None, None)
        assert svc._normalize_multi_reference(None) == (None, None)

    def test_uppercase_and_trim(self) -> None:
        ref, err = svc._normalize_multi_reference("  acme-2026-Q4  ")
        assert err is None
        assert ref == "ACME-2026-Q4"

    def test_rejects_oversize(self) -> None:
        # 64 chars would overflow Quote.client_reference (64) once the
        # ``-L<n>-{AIR,HOT}`` per-leg suffix is appended. The cap is
        # ``64 - len("-L7-HOT") = 57`` - one over must be rejected, exactly
        # at the cap must pass.
        ref, err = svc._normalize_multi_reference("A" * 58)
        assert ref is None
        assert err is not None
        ref, err = svc._normalize_multi_reference("A" * 57)
        assert err is None
        assert ref == "A" * 57

    def test_cap_leaves_room_for_per_leg_suffix(self) -> None:
        """Max-length base + worst-case suffix must fit Quote.client_reference."""
        from app.models import Quote

        column_len = Quote.__table__.c.client_reference.type.length
        base = "A" * svc.MAX_MULTI_REFERENCE_LENGTH
        # Worst case: 7 legs (single digit) and 3-letter mode tag.
        assert len(f"{base}-L7-HOT") <= column_len

    def test_rejects_bad_chars(self) -> None:
        ref, err = svc._normalize_multi_reference("WAT*!")
        assert ref is None
        assert err is not None


def test_auto_assigned_multi_reference_starts_at_scmq0001(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    result = svc.compute_sc_multileg(_form(), user, request_ip="127.0.0.1")
    assert result["session"].multi_reference == "SCMQ0001"


def test_auto_assigned_multi_reference_increments(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    s1 = svc.compute_sc_multileg(_form(), user, request_ip="127.0.0.1")
    s2 = svc.compute_sc_multileg(_form(), user, request_ip="127.0.0.1")
    s3 = svc.compute_sc_multileg(_form(), user, request_ip="127.0.0.1")
    assert s1["session"].multi_reference == "SCMQ0001"
    assert s2["session"].multi_reference == "SCMQ0002"
    assert s3["session"].multi_reference == "SCMQ0003"


def test_customer_supplied_multi_reference_used_verbatim(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    form = _form(multi_reference="ACME-2026-Q4")
    result = svc.compute_sc_multileg(form, user, request_ip="127.0.0.1")
    assert result["session"].multi_reference == "ACME-2026-Q4"


def test_duplicate_customer_reference_rejected(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    svc.compute_sc_multileg(
        _form(multi_reference="DUP-001"), user, request_ip="127.0.0.1"
    )
    with pytest.raises(ValueError, match="already in use"):
        svc.compute_sc_multileg(
            _form(multi_reference="DUP-001"),
            user,
            request_ip="127.0.0.1",
        )


def test_multi_reference_stamped_on_per_leg_quotes(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each underlying Quote row carries the unified ref + leg suffix.

    Bypasses the stub: confirms the orchestrator passes the right
    ``client_reference`` value down to ``create_quote``. The leg-+-mode
    suffix is what keeps the per-user UNIQUE(user_id, client_reference)
    constraint from blowing up when one customer submits two sessions
    in a row.
    """

    _seed_reference(app)
    user = _make_user()
    calls = _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )
    svc.compute_sc_multileg(
        _form(multi_reference="JOB-7"), user, request_ip="127.0.0.1"
    )
    # One active leg fires two create_quote calls (Air + Hotshot).
    assert {call["client_reference"] for call in calls} == {
        "JOB-7-L1-AIR",
        "JOB-7-L1-HOT",
    }


def test_invalid_multi_reference_raises(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(monkeypatch, {})
    with pytest.raises(ValueError):
        svc.compute_sc_multileg(
            _form(multi_reference="bad*chars"),
            user,
            request_ip="127.0.0.1",
        )


def test_retry_path_re_stamps_per_leg_quote_client_reference(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent collision must NOT leave stale per-leg client_refs.

    Simulates the race where two SC users grab SCMQ0001 simultaneously
    by pre-seeding a session with that reference. The orchestrator's
    first flush fails on UNIQUE(multi_reference); the retry path picks
    SCMQ0002 and must update each leg's already-committed Quote row so
    a customer looking up the leg by ``SCMQ0002-L1-AIR`` finds it (and
    a stale ``SCMQ0001-L1-AIR`` no longer points at this session).
    """

    from app.models import Quote

    _seed_reference(app)
    user = _make_user()
    _stub_create_quote(
        monkeypatch, {("Air", "98101"): 300.0, ("Hotshot", "98101"): 250.0}
    )

    # Reserve SCMQ0001 with another user so our orchestrator's first
    # flush hits IntegrityError and retries. Using a different user
    # avoids tripping the Quote-level UNIQUE(user_id, client_reference)
    # constraint inside create_quote() before the retry can fire.
    other = User(
        email="sc-other@example.com",
        name="SC Other",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(other)
    db.session.flush()
    db.session.add(
        SCQuoteSession(
            user_id=other.id,
            grand_total=0.0,
            payload_json="{}",
            multi_reference="SCMQ0001",
        )
    )
    db.session.commit()

    result = svc.compute_sc_multileg(
        _form(), user, request_ip="127.0.0.1"
    )
    assert result["session"].multi_reference == "SCMQ0002"

    # The per-leg Quote rows for the new session must carry the FINAL
    # reference, not the stale SCMQ0001 one we initially stamped.
    leg = result["legs"][0]
    db.session.refresh(leg.air_quote)
    db.session.refresh(leg.hotshot_quote)
    assert leg.air_quote.client_reference == "SCMQ0002-L1-AIR"
    assert leg.hotshot_quote.client_reference == "SCMQ0002-L1-HOT"
    # And no orphan Quote row claims the now-conflicting SCMQ0001 ref
    # for this user (would surface as a spurious match in the customer
    # lookup).
    stale = Quote.query.filter_by(
        user_id=user.id, client_reference="SCMQ0001-L1-AIR"
    ).first()
    assert stale is None
