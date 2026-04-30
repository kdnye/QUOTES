import pytest

"""Unit tests for hotshot quote surcharge composition."""

from types import SimpleNamespace

from app.models import FuelSurcharge
from app.quote import logic_hotshot


def test_calculate_hotshot_quote_uses_surcharge_pipeline_for_standard_zone(monkeypatch):
    """Ensure hotshot totals use base + base surcharge + VSC + accessorial.

    Inputs:
        monkeypatch: pytest fixture used to replace distance and dynamic VSC helpers.

    Outputs:
        None. Asserts quote structure and computed totals.

    External dependencies:
        Calls ``app.quote.logic_hotshot.calculate_hotshot_quote`` and patches
        ``app.quote.logic_hotshot.get_distance_miles`` and
        ``app.quote.logic_hotshot.get_dynamic_vsc_pct``.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "BASE_SURCHARGE_PCT", 0.315)

    fuel_row = FuelSurcharge(padd_region="PADD4", current_rate=5.270)

    captured = {}

    def _dynamic_vsc(**kwargs):
        captured.update(kwargs)
        # 5.270% less base 31.5% => 18.5% dynamic VSC.
        assert fuel_row.padd_region == "PADD4"
        assert fuel_row.current_rate == pytest.approx(5.270)
        return 0.185

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", _dynamic_vsc)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=20.0,
        accessorial_total=10.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0,
            fuel_pct=0.99,
            weight_break=100.0,
            min_charge=50.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
    )

    assert captured["dest_zone"] == "7"
    assert result["base_rate"] == 100.0
    assert result["fuel_surcharge_base_amount"] == 31.5
    assert result["vsc_amount"] == 18.5
    assert result["total_fsc_applied"] == pytest.approx(0.5)
    assert result["quote_total"] == 160.0
    assert result["dest_zone"] == "7"
    assert result["warning_metadata"] == []


def test_calculate_hotshot_quote_zone_x_uses_override_rates_and_surcharges(monkeypatch):
    """Ensure zone X still uses hardcoded rates before surcharge pipeline.

    Inputs:
        monkeypatch: pytest fixture used to replace distance and dynamic VSC helpers.

    Outputs:
        None. Asserts zone-X pricing and surcharge details.

    External dependencies:
        Calls ``app.quote.logic_hotshot.calculate_hotshot_quote`` and patches
        ``app.quote.logic_hotshot.get_distance_miles`` and
        ``app.quote.logic_hotshot.get_dynamic_vsc_pct``.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 10.0)
    monkeypatch.setattr(logic_hotshot, "BASE_SURCHARGE_PCT", 0.2)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=1.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=1.0,
            fuel_pct=0.25,
            weight_break=None,
            min_charge=1.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
    )

    assert result["per_lb"] == logic_hotshot.ZONE_X_PER_LB_RATE
    assert result["per_mile"] == logic_hotshot.ZONE_X_PER_MILE_RATE
    assert result["min_charge"] == 52.0
    assert result["base_rate"] == 52.0
    assert result["fuel_surcharge_base_amount"] == 10.4
    assert result["vsc_amount"] == 0.0
    assert result["quote_total"] == 62.4


def test_calculate_hotshot_quote_uses_national_fallback_when_dest_zone_missing(
    monkeypatch,
):
    """Ensure hotshot emits warning metadata when destination zone fallback is used.

    Inputs:
        monkeypatch: pytest fixture used to replace distance and dynamic VSC helpers.

    Outputs:
        None. Asserts fallback destination zone and warning metadata fields.

    External dependencies:
        Calls ``app.quote.logic_hotshot.calculate_hotshot_quote`` and patches
        ``app.quote.logic_hotshot.get_distance_miles`` and
        ``app.quote.logic_hotshot.get_dynamic_vsc_pct``.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 25.0)

    captured = {}

    def _dynamic_vsc(**kwargs):
        captured.update(kwargs)
        return 0.0

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", _dynamic_vsc)

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

    assert captured["dest_zone"] == "NATIONAL"
    assert result["dest_zone"] == "NATIONAL"
    assert result["warning_metadata"][0]["code"] == "HOTSHOT_DEST_ZONE_FALLBACK"


def test_calculate_hotshot_quote_uses_vsc_fallback_when_fuel_surcharge_missing(monkeypatch):
    """Ensure dynamic VSC lookup can fall back when FuelSurcharge row is missing."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 60.0)
    monkeypatch.setattr(logic_hotshot, "BASE_SURCHARGE_PCT", 0.315)

    def _dynamic_vsc(**_kwargs):
        # Missing FuelSurcharge row -> service fallback at 18.5%.
        return 0.185

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", _dynamic_vsc)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="30301",
        destination="99999",
        weight=40.0,
        accessorial_total=5.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=1.0,
            fuel_pct=0.9,
            weight_break=0.0,
            min_charge=20.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=4),
    )

    assert result["fuel_surcharge_base_amount"] == 12.6
    assert result["vsc_amount"] == 7.4
    assert result["total_fsc_applied"] == pytest.approx(0.5)


def test_calculate_hotshot_quote_ignores_legacy_fuel_pct_to_prevent_double_counting(monkeypatch):
    """Ensure totals do not include legacy ``HotshotRate.fuel_pct`` values."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "BASE_SURCHARGE_PCT", 0.315)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.185)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0,
            fuel_pct=9.99,
            weight_break=None,
            min_charge=50.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
    )

    # If legacy fuel_pct were applied again, total would exceed 150.
    assert result["quote_total"] == pytest.approx(150.0)
    assert result["total_fsc_applied"] == pytest.approx(0.5)
