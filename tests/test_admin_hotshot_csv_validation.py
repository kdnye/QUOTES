"""Cross-column CSV-upload validation for HotshotRate rows.

Pure-logic tests for the row validator. ``_parse_csv_rows`` invokes
``spec.row_validator(parsed_data)`` after per-column parsing and surfaces
any returned error with the same ``Row N: ...`` formatting it uses for
column errors, so testing the validator function directly covers the
"upload-time guard" intent (Codex P2 review on PR #296). The TableSpec
wiring is verified by ``test_hotshot_rates_table_spec_wires_validator``.
"""

from __future__ import annotations

from app.admin import TABLE_SPECS, _validate_hotshot_rate_row


def test_validate_hotshot_rate_row_rejects_zone_x_without_per_mile() -> None:
    err = _validate_hotshot_rate_row({"zone": "X", "per_mile": None})
    assert err is not None
    assert "Per Mile is required for Zone X" in err


def test_validate_hotshot_rate_row_rejects_zone_x_with_zero_per_mile() -> None:
    err = _validate_hotshot_rate_row({"zone": "x", "per_mile": 0.0})
    assert err is not None
    assert "Per Mile is required for Zone X" in err


def test_validate_hotshot_rate_row_accepts_zone_x_with_per_mile() -> None:
    assert _validate_hotshot_rate_row({"zone": "X", "per_mile": 6.0192}) is None


def test_validate_hotshot_rate_row_accepts_zones_a_through_j_without_per_mile() -> None:
    for zone in ("A", "B", "C", "J"):
        assert _validate_hotshot_rate_row({"zone": zone, "per_mile": None}) is None


def test_hotshot_rates_table_spec_wires_validator() -> None:
    """Without this, an upload of a Zone X row with blank Per Mile would slip
    through ``_parse_csv_rows`` and only blow up later at quote time."""

    assert TABLE_SPECS["hotshot_rates"].row_validator is _validate_hotshot_rate_row
