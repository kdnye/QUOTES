import pytest

"""Unit tests for hotshot quote surcharge composition.

These tests pin the FSI VSC-Locked workbook math:

* Zones A-J base = ``IF(weight > weight_break, ((weight - WB) * per_lb) + min, min)``
* Zone X base = ``miles * per_mile`` (no per-lb floor, no fuel surcharge)
* Fuel surcharge applies ONLY to Zones A-J (workbook hard-codes ``*1.315``
  on D17; D18 / Zone X has no multiplier; FSI stores Fuel=0 for Zone X)
* VSC zone = ``MAX(origin VSC zone, destination VSC zone)`` (workbook K10)
* NYC override: if dest ZIP is in :data:`logic_hotshot.NYC_FLAT_RATE_ZIPS`,
  the higher of (zone base + fuel) and :data:`logic_hotshot.NYC_FLAT_RATE_USD`
  becomes the freight subtotal; accessorials and VSC apply on top.
"""

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
            per_lb=5.1, per_mile=6.0192, fuel_pct=0.0, weight_break=None, min_charge=10.0
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=1),
        # Stub the VSC zone lookup so the test doesn't hit the real DB
        # under Postgres CI (where the vsc_zones table may not be in the
        # per-test reset schema).
        vsc_zone_lookup=lambda _zip, rate_set=None: 1,
    )

    assert result["miles"] == 24
    assert result["zone"] == "X"
    assert result["min_charge"] == pytest.approx(24 * 6.0192)


def test_calculate_hotshot_quote_a_j_uses_weight_break_formula(monkeypatch):
    """Zones A-J apply the FSI weight-break formula, then *fuel, then VSC.

    weight (300) > weight_break (100) so:
        base = ((300 - 100) * 5) + 50 = 1050
        fuel = 1050 * 0.315 = 330.75
        base_with_fuel = 1380.75
        vsc = 1380.75 * 0.185 = 255.4388
        total = 1380.75 + 255.4388 + 10 = 1646.19
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.185)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=300.0,
        accessorial_total=10.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0,
            fuel_pct=0.315,
            weight_break=100.0,
            min_charge=50.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
        vsc_zone_lookup=lambda _zip, rate_set=None: 7,
    )

    assert result["base_rate"] == pytest.approx(1050.0)
    assert result["fuel_surcharge_base_pct"] == pytest.approx(0.315)
    assert result["fuel_surcharge_base_amount"] == pytest.approx(1050.0 * 0.315)
    assert result["vsc_pct"] == pytest.approx(0.185)
    assert result["vsc_amount"] == pytest.approx(1050.0 * 1.315 * 0.185)
    assert result["quote_total"] == pytest.approx(1050.0 * 1.315 * 1.185 + 10.0)


def test_calculate_hotshot_quote_a_j_under_weight_break_uses_min(monkeypatch):
    """Below the weight break Zones A-J charge the flat min (then *fuel, +VSC).

    weight (20) <= weight_break (100) so base = min_charge = 50.
        fuel = 50 * 0.315 = 15.75
        base_with_fuel = 65.75
        vsc = 65.75 * 0.185 = 12.16375
        total = 65.75 + 12.16375 + 10 = 87.91
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
        vsc_zone_lookup=lambda _zip, rate_set=None: 7,
    )

    assert result["base_rate"] == pytest.approx(50.0)
    assert result["quote_total"] == pytest.approx(50.0 * 1.315 * 1.185 + 10.0)


def test_calculate_hotshot_quote_matches_fsi_workbook_85022_85260(monkeypatch):
    """Pin to the FSI VSC-Locked workbook's cached value for 85022 -> 85260.

    Workbook scenario (Domestic Hotshot Quotes tab, cached D3-D20):
      D3 origin 85022, D4 dest 85260, D5 miles=15.83 -> D6=16 -> D7=Zone B
      D9 weight=100, D11 Specific Time = 1, D12 VSC = 1, all others = 0
      Zone B: min=81.4528, per_lb=0.2464, weight_break=330.57

      weight (100) <= WB (330.57) -> base = min = 81.4528
      fuel = 81.4528 * 0.315 = 25.6576...
      base_with_fuel = 81.4528 * 1.315 = 107.110432
      D17 = 107.110432 + 95 = 202.110432
      VSC% = 0.195 (AZ -> VSC zone 8 -> 19.5%)
      D19 = (D17 - 95) * 0.195 = 107.110432 * 0.195 = 20.88653424
      D20 = 202.110432 + 20.88653424 = 222.99696624

    The app must reproduce $222.99696624.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 15.83)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.195)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="85022",
        destination="85260",
        weight=100.0,
        accessorial_total=95.0,  # Specific Time
        zone_lookup=lambda miles, rate_set=None: "B" if miles == 16 else "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=0.2464,
            fuel_pct=0.315,
            weight_break=330.571429,
            min_charge=81.4528,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=4),
        vsc_zone_lookup=lambda _zip, rate_set=None: 8,
    )

    assert result["miles"] == 16
    assert result["zone"] == "B"
    assert result["base_rate"] == pytest.approx(81.4528)
    assert result["fuel_surcharge_base_amount"] == pytest.approx(81.4528 * 0.315)
    assert result["vsc_amount"] == pytest.approx(107.110432 * 0.195)
    assert result["quote_total"] == pytest.approx(222.99696624)


def test_calculate_hotshot_quote_zone_x_no_fuel_surcharge(monkeypatch):
    """Zone X uses pure miles * per_mile with NO fuel surcharge.

    Workbook D18 = D6 * G10 + accessorials (no *1.315). FSI stores
    Fuel=0 for Zone X. The app must zero out fuel even if the rate row
    still carries a non-zero fuel_pct (defensive: matches the
    spreadsheet's hard-coded behavior).
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 200.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=100.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.1,
            per_mile=6.0192,
            fuel_pct=0.315,  # would be applied under the old runtime
            weight_break=None,
            min_charge=1.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
        vsc_zone_lookup=lambda _zip, rate_set=None: 7,
    )

    expected_base = 200.0 * 6.0192
    assert result["per_lb"] == 5.1
    assert result["per_mile"] == 6.0192
    assert result["base_rate"] == pytest.approx(expected_base)
    assert result["fuel_surcharge_base_amount"] == pytest.approx(0.0)
    assert result["fuel_surcharge_base_pct"] == pytest.approx(0.0)
    assert result["quote_total"] == pytest.approx(expected_base)


def test_calculate_hotshot_quote_zone_x_no_weight_per_lb_floor(monkeypatch):
    """Zone X drops the weight*per_lb floor (workbook D18 is pure per-mile)."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    # weight (5000) * per_lb (5.1) = 25,500 would dominate the per-mile
    # charge (100 * 6.0192 = 601.92) under the OLD max-floor formula.
    # Under the FSI rule the floor never fires.
    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=5000.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.1,
            per_mile=6.0192,
            fuel_pct=0.0,
            weight_break=None,
            min_charge=1.0,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
        vsc_zone_lookup=lambda _zip, rate_set=None: 7,
    )

    assert result["base_rate"] == pytest.approx(100.0 * 6.0192)
    assert result["quote_total"] == pytest.approx(100.0 * 6.0192)


def test_calculate_hotshot_quote_zone_x_null_per_mile_raises(monkeypatch):
    """Zone X row with NULL per_mile is a data-integrity error and must raise."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    with pytest.raises(ValueError, match="Zone X HotshotRate row is missing per_mile"):
        logic_hotshot.calculate_hotshot_quote(
            origin="11111",
            destination="22222",
            weight=1.0,
            accessorial_total=0.0,
            zone_lookup=lambda _miles, rate_set=None: "X",
            rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
                per_lb=5.1,
                per_mile=None,
                fuel_pct=0.315,
                weight_break=None,
                min_charge=1.0,
            ),
            zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
        )


def test_calculate_hotshot_quote_zone_x_honors_custom_rate_set(monkeypatch):
    """A custom rate_set with its own Zone X per_mile overrides the default seed."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 200.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    rate_rows = {
        "default": SimpleNamespace(
            per_lb=5.1, per_mile=6.0192, fuel_pct=0.0, weight_break=None, min_charge=1.0
        ),
        "custom_test": SimpleNamespace(
            per_lb=5.1, per_mile=8.0, fuel_pct=0.0, weight_break=None, min_charge=1.0
        ),
    }

    def rate_lookup(_zone, rate_set=None):
        return rate_rows[rate_set]

    custom_result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=100.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=rate_lookup,
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
        vsc_zone_lookup=lambda _zip, rate_set=None: 7,
        rate_set="custom_test",
    )
    default_result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=100.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=rate_lookup,
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=7),
        vsc_zone_lookup=lambda _zip, rate_set=None: 7,
        rate_set="default",
    )

    assert custom_result["per_mile"] == 8.0
    assert custom_result["min_charge"] == pytest.approx(200.0 * 8.0)
    assert default_result["per_mile"] == 6.0192
    assert default_result["min_charge"] == pytest.approx(200.0 * 6.0192)
    assert custom_result["quote_total"] != default_result["quote_total"]


def test_calculate_hotshot_quote_uses_national_fallback_when_dest_zone_missing(
    monkeypatch,
):
    """Hotshot emits warning metadata when destination zone fallback is used.

    With the FSI ``MAX(origin, dest)`` rule the runtime now resolves both
    endpoints, so a missing-both scenario produces two HOTSHOT_DEST_ZONE_FALLBACK
    entries in warning_metadata.
    """

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
        vsc_zone_lookup=lambda _zip, rate_set=None: None,
    )

    assert result["dest_zone"] == "NATIONAL"
    assert result["origin_vsc_zone"] == "NATIONAL"
    assert result["vsc_zone_used"] == "NATIONAL"
    codes = [w["code"] for w in result["warning_metadata"]]
    assert codes == ["HOTSHOT_DEST_ZONE_FALLBACK", "HOTSHOT_DEST_ZONE_FALLBACK"]


def test_get_vsc_zone_for_zip_can_raise_typed_error_when_missing() -> None:
    """Missing ZIP can raise deterministic typed lookup error when configured.

    Both ``zip_lookup`` and ``vsc_zone_lookup`` are stubbed so the test
    never hits the real DB — important under Postgres CI where the
    ``vsc_zones`` table may not be in the per-test reset schema.
    """

    with pytest.raises(logic_hotshot.VscDestinationZoneLookupError):
        logic_hotshot.get_vsc_zone_for_zip(
            "99999",
            rate_set="default",
            zip_lookup=lambda _zip, rate_set=None: None,
            vsc_zone_lookup=lambda _zip, rate_set=None: None,
            raise_on_missing=True,
        )


def test_calculate_hotshot_quote_a_j_blank_weight_break_falls_back_to_min_over_per_lb(
    monkeypatch,
):
    """A-J rows with NULL weight_break derive WB = min/per_lb (workbook G=F/E).

    Without the fallback, a legacy CSV / admin upload that leaves the
    Weight Break cell blank would silently flatten any A-J quote to
    `min_charge` no matter how heavy the shipment is. Codex flagged
    this regression on PR #328; this test pins the fix so a future
    refactor cannot quietly drop it again.

    Inputs: weight=600, per_lb=0.208, min=79.56, WB=None.
        derived WB = 79.56 / 0.208 = 382.5
        base = ((600 - 382.5) * 0.208) + 79.56
             = 217.5 * 0.208 + 79.56
             = 45.24 + 79.56 = 124.80
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 5.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="11111",
        destination="22222",
        weight=600.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=0.208,
            fuel_pct=0.0,
            weight_break=None,
            min_charge=79.56,
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=1),
        vsc_zone_lookup=lambda _zip, rate_set=None: 1,
    )

    # 79.56 + (600 - 382.5)*0.208 = 79.56 + 45.24 = 124.80
    assert result["base_rate"] == pytest.approx(79.56 + (600 - 382.5) * 0.208)


def test_calculate_hotshot_quote_strips_whitespace_from_zips(monkeypatch) -> None:
    """Accidental whitespace on origin/dest ZIPs is stripped before VSC lookup.

    Without the strip, the VSC zone lookup misses (the stub matches "30301"
    not " 30301 ") and the runtime falls back to "NATIONAL", quietly
    losing the real zone. With the strip both endpoints resolve to their
    numeric zones and `vsc_zone_used` is the larger one.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 50.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    def zip_lookup(zipcode, rate_set=None):
        # The stub explicitly does not strip — that mirrors the live DB
        # lookups; we expect the calling code to normalize.
        if str(zipcode) == "30301":
            return SimpleNamespace(dest_zone=4)
        if str(zipcode) == "90808":
            return SimpleNamespace(dest_zone=10)
        return None

    def vsc_zone_lookup(zipcode, rate_set=None):
        if str(zipcode) == "30301":
            return 4
        if str(zipcode) == "90808":
            return 9
        return None

    result = logic_hotshot.calculate_hotshot_quote(
        origin="  30301 ",
        destination="\t90808\n",
        weight=10.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0, fuel_pct=0.0, weight_break=None, min_charge=50.0
        ),
        zip_lookup=zip_lookup,
        vsc_zone_lookup=vsc_zone_lookup,
    )

    assert result["origin_vsc_zone"] == "4"
    assert result["dest_zone"] == "9"
    assert result["vsc_zone_used"] == "9"
    # warning_metadata should be empty — both endpoints resolved.
    assert result["warning_metadata"] == []


def test_hotshot_vsc_uses_max_of_origin_and_destination(monkeypatch) -> None:
    """FSI K10 = MAX(K8, K9) — the higher of origin/dest VSC zones drives FSC.

    Origin VSC zone = 4 (e.g. UT/CO/SC), destination VSC zone = 9 (CA/HI).
    Pre-fix the app used dest only and would have called 0.22; that still
    happens here because dest=9 is the larger. But we also assert the
    captured argument is "9" (not "4") so the MAX logic is actually used,
    and the result dict reports both zones plus the picked one.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 50.0)

    captured = {"zone_passed": None}

    def dynamic_vsc_pct(*, base, miles, zone, dest_zone, rate_set):
        _ = (base, miles, zone, rate_set)
        captured["zone_passed"] = str(dest_zone)
        return 0.22 if str(dest_zone) == "9" else 0.17

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", dynamic_vsc_pct)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="80205",
        destination="90808",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0, fuel_pct=0.315, weight_break=None, min_charge=50.0
        ),
        zip_lookup=lambda zipcode, rate_set=None: SimpleNamespace(
            dest_zone=4 if str(zipcode) == "80205" else 10
        ),
        vsc_zone_lookup=lambda zipcode, rate_set=None: (
            4 if str(zipcode) == "80205" else 9
        ),
    )

    assert result["origin_vsc_zone"] == "4"
    assert result["dest_zone"] == "9"
    assert result["vsc_zone_used"] == "9"
    assert captured["zone_passed"] == "9"
    assert result["vsc_pct"] == pytest.approx(0.22)


def test_hotshot_vsc_max_picks_origin_when_higher(monkeypatch) -> None:
    """When origin VSC zone > dest's, the origin zone wins (MAX semantics)."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 50.0)

    captured = {"zone_passed": None}

    def dynamic_vsc_pct(*, base, miles, zone, dest_zone, rate_set):
        _ = (base, miles, zone, rate_set)
        captured["zone_passed"] = str(dest_zone)
        return 0.205 if str(dest_zone) == "9" else 0.17

    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", dynamic_vsc_pct)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="90808",
        destination="80205",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "A",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.0, fuel_pct=0.315, weight_break=None, min_charge=50.0
        ),
        zip_lookup=lambda zipcode, rate_set=None: SimpleNamespace(
            dest_zone=10 if str(zipcode) == "90808" else 4
        ),
        vsc_zone_lookup=lambda zipcode, rate_set=None: (
            9 if str(zipcode) == "90808" else 4
        ),
    )

    assert result["origin_vsc_zone"] == "9"
    assert result["dest_zone"] == "4"
    assert result["vsc_zone_used"] == "9"
    assert captured["zone_passed"] == "9"
    assert result["vsc_pct"] == pytest.approx(0.205)


def test_hotshot_nyc_flat_rate_override(monkeypatch) -> None:
    """Destination ZIP in NYC list -> $1,100 flat (no fuel) wins via MAX.

    Origin: SCPA (19032), dest: 10001 (Manhattan), 100 mi -> Zone X, weight=20.
    Zone X base = 100 * 6.0192 = 601.92 (no fuel) — LESS than $1,100.
    So the NYC override wins. VSC + accessorials apply on top.
        nyc_base + acc = 1100 + 50 = 1150
        VSC = 1100 * 0.17 = 187
        total = 1100 + 187 + 50 = 1337
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 100.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.17)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="19032",
        destination="10001",
        weight=20.0,
        accessorial_total=50.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.1, per_mile=6.0192, fuel_pct=0.0, weight_break=None, min_charge=1.0
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=3),
        vsc_zone_lookup=lambda _zip, rate_set=None: 3,
    )

    assert result["nyc_override_applied"] is True
    assert result["base_rate"] == pytest.approx(logic_hotshot.NYC_FLAT_RATE_USD)
    assert result["fuel_surcharge_base_amount"] == pytest.approx(0.0)
    assert result["vsc_amount"] == pytest.approx(
        logic_hotshot.NYC_FLAT_RATE_USD * 0.17
    )
    assert result["quote_total"] == pytest.approx(
        logic_hotshot.NYC_FLAT_RATE_USD * 1.17 + 50.0
    )


def test_hotshot_nyc_flat_rate_does_not_apply_when_zone_base_wins(monkeypatch) -> None:
    """Per the workbook's MAX(D17, D18), a giant Zone-X charge beats NYC's $1,100."""

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 500.0)
    monkeypatch.setattr(logic_hotshot, "get_dynamic_vsc_pct", lambda **_kwargs: 0.0)

    result = logic_hotshot.calculate_hotshot_quote(
        origin="19032",
        destination="10001",
        weight=20.0,
        accessorial_total=0.0,
        zone_lookup=lambda _miles, rate_set=None: "X",
        rate_lookup=lambda _zone, rate_set=None: SimpleNamespace(
            per_lb=5.1, per_mile=6.0192, fuel_pct=0.0, weight_break=None, min_charge=1.0
        ),
        zip_lookup=lambda _zip, rate_set=None: SimpleNamespace(dest_zone=3),
        vsc_zone_lookup=lambda _zip, rate_set=None: 3,
    )

    # 500 * 6.0192 = 3009.60, well above the $1,100 NYC override.
    assert result["nyc_override_applied"] is False
    assert result["base_rate"] == pytest.approx(500.0 * 6.0192)


def test_hotshot_missing_vsc_mapping_falls_back_to_national(monkeypatch) -> None:
    """Origin resolves to a real zone, destination falls back to NATIONAL.

    Under MAX(origin, dest) semantics with origin=4 (numeric) and
    dest=NATIONAL (non-numeric), the picked zone is the numeric origin.
    The result still surfaces the destination fallback in warning metadata.
    """

    monkeypatch.setattr(logic_hotshot, "get_distance_miles", lambda *_args: 50.0)

    def zip_lookup(zipcode, rate_set=None):
        if str(zipcode) == "30301":
            return SimpleNamespace(dest_zone=4)
        return None

    captured = {"dest_zone_passed": None}

    def dynamic_vsc_pct(*, base, miles, zone, dest_zone, rate_set):
        _ = (base, miles, zone, rate_set)
        captured["dest_zone_passed"] = dest_zone
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

    assert result["origin_vsc_zone"] == "4"
    assert result["dest_zone"] == "NATIONAL"
    # MAX(numeric origin, "NATIONAL" dest) -> origin wins so dest fallback
    # never poisons the FSC calc.
    assert result["vsc_zone_used"] == "4"
    assert captured["dest_zone_passed"] == "4"
    fallback = [w for w in result["warning_metadata"] if w["code"] == "HOTSHOT_DEST_ZONE_FALLBACK"]
    assert len(fallback) == 1
