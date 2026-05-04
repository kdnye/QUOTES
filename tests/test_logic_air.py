"""Unit tests for air quote surcharge policy and payload fields."""

from types import SimpleNamespace

from app.quote.logic_air import BASE_SURCHARGE_PCT, calculate_air_quote


def test_calculate_air_quote_applies_granular_dynamic_vsc() -> None:
    """Validate air quote totals and metadata when surcharge applies.

    Inputs:
        None.

    Outputs:
        None. Asserts that base FSC applies to linehaul + beyond, and VSC
        applies per-zone across linehaul and beyond components.

    External dependencies:
        Calls ``app.quote.logic_air.calculate_air_quote`` with in-memory
        lookup callbacks instead of database access.
    """

    zip_lookup = lambda zipcode, rate_set=None: SimpleNamespace(
        dest_zone=1 if zipcode == "11111" else 2,
        beyond="ZONE A" if zipcode == "11111" else "ZONE B",
    )
    cost_zone_lookup = lambda concat, rate_set=None: SimpleNamespace(cost_zone="X1")
    air_cost_lookup = lambda zone, rate_set=None: SimpleNamespace(
        min_charge=100.0,
        per_lb=2.0,
        weight_break=10.0,
    )
    beyond_rate_lookup = lambda zone, rate_set=None: 3.0 if zone == "A" else 5.0

    result = calculate_air_quote(
        origin="11111",
        destination="22222",
        weight=20.0,
        accessorial_total=7.0,
        zip_lookup=zip_lookup,
        cost_zone_lookup=cost_zone_lookup,
        air_cost_lookup=air_cost_lookup,
        beyond_rate_lookup=beyond_rate_lookup,
        dynamic_vsc_lookup=lambda zone, rate_set=None: 0.05 if zone == "1" else 0.1,
    )

    assert result["base_rate"] == 120.0
    assert result["beyond_total"] == 8.0
    assert result["fuel_surcharge_base_pct"] == BASE_SURCHARGE_PCT
    assert result["fuel_surcharge_base_amount"] == 40.32
    assert result["vsc_pct"] == 0.1
    assert result["origin_vsc_pct"] == 0.05
    assert result["dest_vsc_pct"] == 0.1
    assert result["vsc_amount"] == 12.65
    assert result["total_fsc_applied"] == BASE_SURCHARGE_PCT + 0.1
    assert result["quote_total"] == 187.97
    assert result["surcharge_applies"] is True
    assert result["surcharge_policy"] == "base_plus_dynamic_vsc"
    assert "31.5%" in result["surcharge_reason"]


def test_calculate_air_quote_error_payload_includes_surcharge_metadata() -> None:
    """Validate that error payloads still include surcharge policy metadata.

    Inputs:
        None.

    Outputs:
        None. Asserts that missing ZIP errors preserve surcharge explanation
        fields for downstream UI/API rendering.

    External dependencies:
        Calls ``app.quote.logic_air.calculate_air_quote`` with failing
        callbacks to simulate lookup failures.
    """

    result = calculate_air_quote(
        origin="00000",
        destination="11111",
        weight=10.0,
        accessorial_total=0.0,
        zip_lookup=lambda _zipcode, rate_set=None: None,
    )

    assert result["error"] == "Origin ZIP code 00000 not found"
    assert result["surcharge_applies"] is True
    assert result["surcharge_policy"] == "base_plus_dynamic_vsc"
    assert result["total_fsc_applied"] == 0.0
