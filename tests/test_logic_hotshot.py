import pytest

"""Unit tests for hotshot quote surcharge composition."""

from types import SimpleNamespace

from app.quote import logic_hotshot


def test_miles_are_ceiling_rounded(monkeypatch):
    """get_distance_miles float is ceiled before use (23.1 -> 24 not 23)."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_: 23.1)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=1.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=1.0, fuel_pct=0.0, weight_break=None, min_charge=10.0
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=1),
    )

    assert result["miles"] == 24


def test_calculate_hotshot_quote_applies_rate_fuel_pct_then_vsc(monkeypatch):
    """VSC is applied to the post-fuel subtotal, not the raw base.

    base = max(50, 20*5) = 100
    fuel_surcharge = 100 * 0.315 = 31.5
    base_with_fuel = 131.5
    vsc = 131.5 * 0.185 = 24.3275
    total = 131.5 + 24.3275 + 10 = 165.8275
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.185)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=20.0,
        accessorial_total=10.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0,
            fuel_pct=0.315,
            weight_break=100.0,
            min_charge=50.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
    )

    assert result["base_rate"] == pytest.approx(100.0)
    assert result["fuel_surcharge_base_pct"] == pytest.approx(0.315)
    assert result["fuel_surcharge_base_amount"] == pytest.approx(31.5)
    assert result["vsc_pct"] == pytest.approx(0.185)
    assert result["vsc_amount"] == pytest.approx(131.5 * 0.185)
    assert result["total_fsc_applied"] == pytest.approx((31.5 + 131.5 * 0.185) / 100.0)
    assert result["quote_total"] == pytest.approx(131.5 + 131.5 * 0.185 + 10.0)
    assert result["dest_zone"] == "7"
    assert result["warning_metadata"] == []


def test_calculate_hotshot_quote_zone_x_uses_override_rates(monkeypatch):
    """Zone X overrides per_lb and per_mile; fuel_pct still comes from rate table.

    miles=10, ZONE_X_PER_MILE_RATE=6.0192 -> min_charge=60.192
    ZONE_X_PER_LB_RATE=5.1, weight=1 -> weight_cost=5.1
    base = max(60.192, 5.1) = 60.192
    fuel_surcharge = 60.192 * 0.315 = 18.96048
    base_with_fuel = 79.15248
    vsc = 79.15248 * 0.0 = 0
    total = 79.15248
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 10.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=1.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=1.0,
            fuel_pct=0.315,
            weight_break=None,
            min_charge=1.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
    )

    expected_base = 10.0 * logic_hotshot.ZONE_X_PER_MILE_RATE
    expected_fuel = expected_base * 0.315
    assert result["per_lb"] == logic_hotshot.ZONE_X_PER_LB_RATE
    assert result["per_mile"] == logic_hotshot.ZONE_X_PER_MILE_RATE
    assert result["base_rate"] == pytest.approx(expected_base)
    assert result["fuel_surcharge_base_amount"] == pytest.approx(expected_fuel)
    assert result["vsc_amount"] == pytest.approx(0.0)
    assert result["quote_total"] == pytest.approx(expected_base + expected_fuel)


def test_calculate_hotshot_quote_uses_national_fallback_when_dest_zone_missing(
    monkeypatch,
):
    """Hotshot emits warning metadata when destination zone fallback is used."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 25.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="99999",
        weight=5.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=2.0,
            fuel_pct=0.0,
            weight_break=0.0,
            min_charge=20.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: None,
    )

    assert result["dest_zone"] == "NATIONAL"
    assert result["warning_metadata"][0]["code"] == "HOTSHOT_DEST_ZONE_FALLBACK"


def test_calculate_hotshot_quote_rate_fuel_pct_affects_vsc_base(monkeypatch):
    """Confirms rate.fuel_pct is used and VSC is compounded on top of it.

    base = 100, fuel_pct=0.315 -> base_with_fuel=131.5
    vsc = 131.5 * 0.185 = 24.3275
    total = 131.5 + 24.3275 = 155.8275

    If fuel_pct were incorrectly ignored, total would be 100 + 18.5 = 118.5.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.185)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0,
            fuel_pct=0.315,
            weight_break=None,
            min_charge=50.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
    )

    assert result["quote_total"] == pytest.approx(131.5 * 1.185)
    assert result["quote_total"] != pytest.approx(118.5)
