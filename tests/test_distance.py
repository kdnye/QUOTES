"""Tests for the distance helper utilities."""

from app.quote.distance import _sanitize_zip


def test_sanitize_zip_truncates_zip_plus_four() -> None:
    """It should keep the first five digits for ZIP+4 input."""

    assert _sanitize_zip("12345-6789") == "12345,USA"


def test_sanitize_zip_accepts_integer() -> None:
    """It should accept integer ZIP codes and normalize them."""

    assert _sanitize_zip(90210) == "90210,USA"


def test_sanitize_zip_rejects_short_input() -> None:
    """It should reject values with fewer than five digits."""

    assert _sanitize_zip("1234") is None
