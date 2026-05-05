"""Unit tests for air quote surcharge policy and payload fields."""

from types import SimpleNamespace

import pytest

from app.quote.logic_air import _normalize_zip_lookup_key, calculate_air_quote


def test_calculate_air_quote_applies_origin_zone_fsc() -> None:
    """Validate air quote totals when origin-zone FSC is the only surcharge.

    base = (20-10)*2 + 100 = 120
    beyond_total = 3 + 5 = 8
    total_base_freight = 128
    fsc_pct = origin_vsc_pct = 0.05
    fsc_amount = 128 * 0.05 = 6.4
    quote_total = 128 + 6.4 + 7 = 141.4
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
        vsc_zone_lookup=lambda zipcode, rate_set=None: 1 if zipcode == "11111" else 8,
        dynamic_vsc_lookup=lambda zone, rate_set=None: 0.05 if zone == "1" else 0.1,
    )

    assert result["base_rate"] == 120.0
    assert result["beyond_total"] == 8.0
    assert result["fuel_surcharge_base_pct"] == 0.0
    assert result["fuel_surcharge_base_amount"] == 0.0
    assert result["vsc_pct"] == pytest.approx(0.05)
    assert result["origin_vsc_pct"] == pytest.approx(0.05)
    assert result["dest_vsc_pct"] == pytest.approx(0.1)
    assert result["vsc_amount"] == pytest.approx(6.4)
    assert result["total_fsc_applied"] == pytest.approx(0.05)
    assert result["quote_total"] == pytest.approx(141.4)
    assert result["surcharge_applies"] is True
    assert result["surcharge_policy"] == "origin_zone_fsc"
    assert "31.5%" not in result["surcharge_reason"]


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
    assert result["surcharge_policy"] == "origin_zone_fsc"
    assert result["total_fsc_applied"] == 0.0


def test_calculate_air_quote_uses_vsc_zone_not_air_zone_for_90808() -> None:
    """Ensure air surcharge lookup uses VSC zone mapping (90808 -> 9) not air zone 10."""

    zip_lookup = lambda zipcode, rate_set=None: SimpleNamespace(
        dest_zone=4 if zipcode == "30301" else 10,
        beyond="ZONE A" if zipcode == "30301" else "ZONE B",
    )
    vsc_zone_lookup = lambda zipcode, rate_set=None: 4 if zipcode == "30301" else 9

    seen = {"zones": []}

    def dynamic_vsc_lookup(zone, rate_set=None):
        seen["zones"].append(str(zone))
        return 0.05 if str(zone) == "4" else 0.22 if str(zone) == "9" else 0.195

    result = calculate_air_quote(
        origin="30301",
        destination="90808",
        weight=20.0,
        accessorial_total=0.0,
        zip_lookup=zip_lookup,
        cost_zone_lookup=lambda concat, rate_set=None: SimpleNamespace(cost_zone="X1"),
        air_cost_lookup=lambda zone, rate_set=None: SimpleNamespace(
            min_charge=100.0, per_lb=2.0, weight_break=10.0
        ),
        beyond_rate_lookup=lambda zone, rate_set=None: 0.0,
        vsc_zone_lookup=vsc_zone_lookup,
        dynamic_vsc_lookup=dynamic_vsc_lookup,
    )

    assert seen["zones"] == ["4", "9"]
    assert result["vsc_pct"] == pytest.approx(0.05)
    assert result["dest_vsc_pct"] == pytest.approx(0.22)


def test_calculate_air_quote_errors_when_destination_vsc_zone_missing() -> None:
    """Missing destination VSC zone returns defined error payload."""

    zip_lookup = lambda zipcode, rate_set=None: SimpleNamespace(
        dest_zone=4 if zipcode == "30301" else 10,
        beyond="ZONE A" if zipcode == "30301" else "ZONE B",
    )

    result = calculate_air_quote(
        origin="30301",
        destination="90808",
        weight=20.0,
        accessorial_total=0.0,
        zip_lookup=zip_lookup,
        cost_zone_lookup=lambda concat, rate_set=None: SimpleNamespace(cost_zone="X1"),
        air_cost_lookup=lambda zone, rate_set=None: SimpleNamespace(
            min_charge=100.0, per_lb=2.0, weight_break=10.0
        ),
        beyond_rate_lookup=lambda zone, rate_set=None: 0.0,
        vsc_zone_lookup=lambda zipcode, rate_set=None: (
            4 if zipcode == "30301" else None
        ),
        dynamic_vsc_lookup=lambda zone, rate_set=None: 0.05,
    )

    assert result["error"] == "Destination ZIP code 90808 missing valid vsc_zone"
    assert result["surcharge_policy"] == "origin_zone_fsc"


def test_normalize_zip_lookup_key_accepts_zip_plus_four() -> None:
    """Normalize ZIP+4 values to the first 5 digits for DB lookups."""

    assert _normalize_zip_lookup_key("90808-1234") == "90808"


def test_normalize_zip_lookup_key_rejects_non_numeric_zip() -> None:
    """Reject malformed ZIP inputs that are not numeric after normalization."""

    assert _normalize_zip_lookup_key("9080A") is None
