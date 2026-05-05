import pytest

"""Unit tests for hotshot quote surcharge composition."""

from types import SimpleNamespace

from app.quote import logic_hotshot


def test_miles_are_ceiling_rounded(monkeypatch):
    """Ceiled miles flow through to result, zone lookup, and Zone X min charge.

    Raw 23.1 -> ceil -> 24. The zone_lookup returns 'X' only when it receives
    24, so a wrong (unrounded) value would silently fall through to zone 'A'
    and the min_charge assertion would also fail.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_: 23.1)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=1.0,
        accessorial_total=0.0,
        zone_lookup=lambda miles, rate_set=None: "X" if miles == 24 else "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=1.0, fuel_pct=0.0, weight_break=None, min_charge=10.0
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=1),
    )

    assert result["miles"] == 24
    assert result["zone"] == "X"
    assert result["min_charge"] == pytest.approx(
        24 * logic_hotshot.ZONE_X_PER_MILE_RATE
    )


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


def test_get_vsc_zone_for_zip_can_raise_typed_error_when_missing() -> None:
    """Missing ZIP can raise deterministic typed lookup error when configured."""

    with pytest.raises(logic_hotshot.VscDestinationZoneLookupError):
        logic_hotshot.get_vsc_zone_for_zip(
            "99999",
            rate_set="default",
            zip_lookup=lambda _zip, rate_set=None: None,
            raise_on_missing=True,
        )


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


def test_hotshot_uses_vsc_zone_mapping_for_90808_and_applies_22pct(monkeypatch) -> None:
    """Use ZIP fixture mapping where 90808 has air zone 10 and VSC zone 9.

    Inputs:
        origin: "30301" and destination: "90808".
        ZIP fixture map: destination ``dest_zone`` is 10 for air path compatibility.
        VSC fixture map: destination ``vsc_zone`` is 9, which resolves to 22%.

    Outputs:
        Ensures dynamic surcharge uses VSC zone ``9`` and applies ``0.22`` rather
        than an air-zone-derived ``0.195`` percentage.

    External dependencies:
        Calls ``app.quote.logic_hotshot.calculate_hotshot_quote`` and stubs
        lookup callbacks only.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 50.0)

    zip_zone_fixture = {
        "30301": SimpleNamespace(dest_zone=4),
        "90808": SimpleNamespace(dest_zone=10),
    }
    captured = {"zone": None}

    def zip_lookup(zipcode, rate_set=None):
        return zip_zone_fixture.get(str(zipcode))

    def dynamic_vsc_pct(*, base, miles, zone, dest_zone, rate_set):
        _ = (base, miles, zone, rate_set)
        captured["zone"] = str(dest_zone)
        return 0.22 if str(dest_zone) == "9" else 0.195

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", dynamic_vsc_pct)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="30301",
        destination="90808",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0,
            fuel_pct=0.315,
            weight_break=None,
            min_charge=50.0,
        ),
        zip_lookup=zip_lookup,
        vsc_zone_lookup=lambda zipcode, rate_set=None: (
            4 if str(zipcode) == "30301" else 9
        ),
    )

    assert result["dest_zone"] == "9"
    assert captured["zone"] == "9"
    assert result["vsc_pct"] == pytest.approx(0.22)


def test_hotshot_missing_vsc_mapping_falls_back_to_national(monkeypatch) -> None:
    """Missing destination VSC mapping falls back to NATIONAL with warning metadata."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 50.0)

    def zip_lookup(zipcode, rate_set=None):
        if str(zipcode) == "30301":
            return SimpleNamespace(dest_zone=4)
        return None

    captured = {"dest_zone": None}

    def dynamic_vsc_pct(*, base, miles, zone, dest_zone, rate_set):
        _ = (base, miles, zone, rate_set)
        captured["dest_zone"] = dest_zone
        return 0.0

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", dynamic_vsc_pct)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="30301",
        destination="90808",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0, fuel_pct=0.315, weight_break=None, min_charge=50.0
        ),
        zip_lookup=zip_lookup,
        vsc_zone_lookup=lambda zipcode, rate_set=None: (
            4 if str(zipcode) == "30301" else None
        ),
    )

    assert result["dest_zone"] == "NATIONAL"
    assert captured["dest_zone"] == "NATIONAL"
    assert result["warning_metadata"][0]["code"] == "HOTSHOT_DEST_ZONE_FALLBACK"
