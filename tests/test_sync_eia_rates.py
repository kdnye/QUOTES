"""Tests for strict EIA payload validation used by sync_eia_rates script."""

from scripts.sync_eia_rates import extract_latest_point


def test_extract_latest_point_returns_normalized_date_and_float_rate() -> None:
    """Return period and float when payload has expected structure."""

    payload = {
        "response": {
            "data": [
                {"period": "2026-04-20", "value": "3.891"},
            ]
        }
    }

    period, rate = extract_latest_point(payload, series_id="SERIES.ONE")

    assert period == "2026-04-20"
    assert rate == 3.891


def test_extract_latest_point_rejects_missing_data_list() -> None:
    """Raise ValueError when API payload omits required data list."""

    payload = {"response": {}}

    try:
        extract_latest_point(payload, series_id="SERIES.MISSING")
        assert False, "Expected ValueError for missing data list"
    except ValueError as exc:
        assert "missing 'data' list" in str(exc)


def test_extract_latest_point_rejects_empty_data() -> None:
    """Raise ValueError when API payload data list is empty."""

    payload = {"response": {"data": []}}

    try:
        extract_latest_point(payload, series_id="SERIES.EMPTY")
        assert False, "Expected ValueError for empty data"
    except ValueError as exc:
        assert "data list is empty" in str(exc)


def test_extract_latest_point_rejects_non_numeric_value() -> None:
    """Raise ValueError when API value cannot be converted to float."""

    payload = {"response": {"data": [{"period": "2026-04", "value": "n/a"}]}}

    try:
        extract_latest_point(payload, series_id="SERIES.BADVAL")
        assert False, "Expected ValueError for non-numeric value"
    except ValueError as exc:
        assert "non-numeric" in str(exc)
