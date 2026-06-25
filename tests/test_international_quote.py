"""Unit tests for ``app/services/international_quote.py``.

The international stub mirrors the FSI VSC-Locked workbook's
``International Quotes!R21`` math without touching the DB. All tests
inject a stubbed ``lane_lookup`` so they run without seeding the
``sc_international_lanes`` table.
"""

from types import SimpleNamespace

import pytest

from app.services.international_quote import (
    INTL_HOTSHOT_CONFIRM_THRESHOLD_USD,
    calculate_international_quote,
)


def _stub_lane(**overrides):
    base = dict(
        destination="Australia - Adelaide",
        country="Australia",
        notes="Door to Door",
        rate_class="Standard",
        lab_code="SCAZ",
        airport_code_1="ADL",
        airport_code_2=None,
        airport_code_3=None,
        min_charge=2950.0,
        per_lb=10.5,
        weight_break=2950.0 / 10.5,  # ≈ 280.952
        cost_per_km_over_80=1.25,
        special_notes="Volumn pricing applies over 800 lbs, email for quote",
        rate_set="science_care",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_under_weight_break_charges_min_only():
    """weight (100) <= weight_break (281) -> base = min, no per_lb component."""
    lane = _stub_lane()
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=10.0,
        lane_lookup=lambda **_: lane,
    )
    assert result.base == pytest.approx(2950.0)
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)
    assert result.quote_total == pytest.approx(2950.0)
    assert result.error is None


def test_over_weight_break_applies_per_lb_overage():
    """weight (500) > weight_break (281) -> (500-281)*10.5 + 2950."""
    lane = _stub_lane(weight_break=281.0)
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=500.0,
        km_to_airport=20.0,
        lane_lookup=lambda **_: lane,
    )
    assert result.base == pytest.approx((500 - 281) * 10.5 + 2950.0)


def test_intl_hotshot_surcharge_kicks_in_above_80km():
    """Door-to-Door + Standard + km>80 -> (round(km)-80) * cost_per_km."""
    lane = _stub_lane(cost_per_km_over_80=1.25)
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=130.4,  # round -> 130
        lane_lookup=lambda **_: lane,
    )
    assert result.intl_hotshot_surcharge == pytest.approx((130 - 80) * 1.25)
    assert result.quote_total == pytest.approx(2950.0 + (130 - 80) * 1.25)


def test_intl_hotshot_surcharge_zero_at_or_under_80km():
    """km <= 80 -> no surcharge even on a Door-to-Door Standard lane."""
    lane = _stub_lane()
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=80.0,
        lane_lookup=lambda **_: lane,
    )
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)


def test_door_to_airport_never_applies_hotshot_surcharge():
    """Door-to-Airport leg skips the int'l hotshot calc regardless of km."""
    lane = _stub_lane(notes="Door to Airport")
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=500.0,
        lane_lookup=lambda **_: lane,
    )
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)
    assert result.quote_total == pytest.approx(2950.0)


def test_missing_km_falls_back_to_warning_when_no_airports():
    """Door-to-Door + no airport codes -> warning, no surcharge."""
    lane = _stub_lane(
        airport_code_1=None, airport_code_2=None, airport_code_3=None
    )
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        lane_lookup=lambda **_: lane,
        distance_lookup=lambda *_a, **_kw: None,  # never called
    )
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)
    assert any("airport" in w.lower() for w in result.warnings)


def test_auto_resolve_picks_nearest_airport_via_distance_lookup():
    """Multi-airport lane: runtime asks the lookup for each, picks MIN.

    Mirrors the workbook's ``AA8 = MIN(W9:W18)`` pattern. ADL is 95 km
    from Adelaide; MEL is 730 km (used here as a stand-in to test the
    MIN). The runtime should pick ADL and surface it as
    ``picked_airport``, then run the surcharge against 95 km not 730.
    """
    lane = _stub_lane(
        destination="Australia - Adelaide",
        airport_code_1="ADL",
        airport_code_2="MEL",
        airport_code_3=None,
        cost_per_km_over_80=1.25,
    )
    calls = []

    def fake_distance(origin, destination):
        calls.append((origin, destination))
        return {"ADL Airport": 95.0, "MEL Airport": 730.0}[origin]

    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        lane_lookup=lambda **_: lane,
        distance_lookup=fake_distance,
    )

    # Both airports were queried; ADL won.
    assert {call[0] for call in calls} == {"ADL Airport", "MEL Airport"}
    # Both queries hit the same city/country string.
    assert all(call[1] == "City of Adelaide, Australia" for call in calls)
    assert result.km_to_airport == pytest.approx(95.0)
    assert result.picked_airport == "ADL"
    # Surcharge = (95 - 80) * 1.25 = 18.75
    assert result.intl_hotshot_surcharge == pytest.approx((95 - 80) * 1.25)


def test_auto_resolve_uses_destination_city_override():
    """``destination_city`` argument wins over the parsed display string."""
    lane = _stub_lane(
        destination="Australia - Sydney",  # display says Sydney
        airport_code_1="MEL",
    )

    def fake_distance(origin, destination):
        # Caller passes Melbourne, not Sydney — confirm it gets through.
        assert destination == "City of Melbourne, Australia"
        return 25.0

    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        destination_city="Melbourne",
        lane_lookup=lambda **_: lane,
        distance_lookup=fake_distance,
    )
    assert result.km_to_airport == pytest.approx(25.0)
    assert result.picked_airport == "MEL"


def test_auto_resolve_dedupes_duplicate_airport_codes():
    """Lanes with repeat airport codes (case/whitespace variants) call once.

    A lane carrying ``("ADL", "adl", " ADL ")`` should send one Google
    request, not three. The helper normalizes + dedups before fetching.
    """
    lane = _stub_lane(
        airport_code_1="ADL",
        airport_code_2="adl",
        airport_code_3=" ADL ",
    )
    calls = []

    def fake_distance(origin, destination):
        calls.append((origin, destination))
        return 95.0

    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        lane_lookup=lambda **_: lane,
        distance_lookup=fake_distance,
    )

    assert len(calls) == 1
    assert calls[0][0] == "ADL Airport"
    assert result.picked_airport == "ADL"
    assert result.km_to_airport == pytest.approx(95.0)


def test_auto_resolve_falls_back_to_warning_when_lookup_fails():
    """Lookup returns None for every airport -> warning, no surcharge."""
    lane = _stub_lane(airport_code_1="ADL")
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        lane_lookup=lambda **_: lane,
        distance_lookup=lambda *_a, **_kw: None,
    )
    assert result.km_to_airport is None
    assert result.picked_airport is None
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)
    assert any("google" in w.lower() for w in result.warnings)


def test_caller_supplied_km_skips_auto_resolve():
    """When ``km_to_airport`` is passed, the distance lookup is never called."""
    lane = _stub_lane(airport_code_1="ADL", cost_per_km_over_80=1.25)
    distance_calls = []

    def fake_distance(origin, destination):
        distance_calls.append((origin, destination))
        return 999.0  # would dominate the MIN if called

    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=100.0,
        lane_lookup=lambda **_: lane,
        distance_lookup=fake_distance,
    )
    assert distance_calls == []
    assert result.km_to_airport == pytest.approx(100.0)
    assert result.picked_airport is None  # caller override -> no pick
    assert result.intl_hotshot_surcharge == pytest.approx((100 - 80) * 1.25)


def test_auto_resolve_skipped_for_door_to_airport_lanes():
    """Door-to-Airport lanes skip the lookup entirely (no surcharge applies)."""
    lane = _stub_lane(notes="Door to Airport", airport_code_1="ADL")
    distance_calls = []

    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        lane_lookup=lambda **_: lane,
        distance_lookup=lambda *args, **kwargs: distance_calls.append(args) or 1000.0,
    )
    assert distance_calls == []
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)


def test_surcharge_over_750_flags_confirmation_threshold():
    """Workbook Z11 — surcharge > $750 requires FSI confirmation."""
    # 1.25/km * 700 km extra = 875 -> requires confirmation
    lane = _stub_lane(cost_per_km_over_80=1.25)
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=780.0,  # surcharge = (780-80)*1.25 = 875
        lane_lookup=lambda **_: lane,
    )
    assert result.intl_hotshot_surcharge == pytest.approx(700 * 1.25)
    assert result.intl_hotshot_surcharge > INTL_HOTSHOT_CONFIRM_THRESHOLD_USD
    assert result.requires_confirmation is True
    assert any("confirm" in w.lower() for w in result.warnings)


def test_missing_lane_returns_error_payload():
    """Lane not in the table -> error string, no quote."""
    result = calculate_international_quote(
        destination="Atlantis - Capital",
        lab_code="SCAZ",
        weight_lb=200.0,
        lane_lookup=lambda **_: None,
    )
    assert result.error is not None
    assert result.quote_total == pytest.approx(0.0)
    assert result.lane is None


def test_destination_and_lab_normalized_at_entry():
    """``destination`` is stripped and ``lab_code`` is stripped + uppercased.

    The result payload and the error message both reflect the canonical
    keys so downstream consumers don't have to second-guess what the
    user typed. The lane_lookup stub receives the normalized values.
    """

    captured = {"destination": None, "lab_code": None, "rate_set": None}

    def lane_lookup(*, destination, lab_code, rate_set):
        captured["destination"] = destination
        captured["lab_code"] = lab_code
        captured["rate_set"] = rate_set
        return None

    result = calculate_international_quote(
        destination="  Australia - Adelaide ",
        lab_code=" scaz\n",
        weight_lb=100.0,
        lane_lookup=lane_lookup,
    )

    assert captured["destination"] == "Australia - Adelaide"
    assert captured["lab_code"] == "SCAZ"
    assert result.destination == "Australia - Adelaide"
    assert result.lab_code == "SCAZ"
    assert "Australia - Adelaide" in result.error
    assert "SCAZ" in result.error


def test_excel_equivalent_rounding_for_halfway_km():
    """Workbook ``AA10`` uses Excel ROUND (half away from zero).

    Python's banker's ``round(80.5)`` returns ``80``, which would make
    the surcharge $0 instead of $1.25. The runtime uses
    ``int(km + 0.5)`` so the halfway case matches the workbook.
    """
    lane = _stub_lane(cost_per_km_over_80=1.25)
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=81.5,  # round (banker's) -> 82; int(81.5+0.5) -> 82
        lane_lookup=lambda **_: lane,
    )
    assert result.intl_hotshot_surcharge == pytest.approx((82 - 80) * 1.25)

    result_half = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=80.5,  # banker's -> 80 (no charge); Excel -> 81
        lane_lookup=lambda **_: lane,
    )
    # int(80.5 + 0.5) = 81 -> (81-80)*1.25 = 1.25, matches the workbook.
    assert result_half.intl_hotshot_surcharge == pytest.approx(1.25)
