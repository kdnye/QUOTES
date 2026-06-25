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


def test_missing_km_emits_warning_for_door_to_door():
    """Door-to-Door + km not supplied -> warning, no surcharge."""
    lane = _stub_lane()
    result = calculate_international_quote(
        destination=lane.destination,
        lab_code=lane.lab_code,
        weight_lb=100.0,
        km_to_airport=None,
        lane_lookup=lambda **_: lane,
    )
    assert result.intl_hotshot_surcharge == pytest.approx(0.0)
    assert any("km" in w.lower() for w in result.warnings)


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
