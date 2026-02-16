"""Tests for ZIP validation backed by Google Places."""

from __future__ import annotations

from typing import Any

import pytest

from app.quote import zip_validation


class _FakeResponse:
    """Simple response stub for requests.get in ZIP validation tests."""

    def __init__(self, payload: dict[str, Any], ok: bool = True) -> None:
        self._payload = payload
        self.ok = ok

    def json(self) -> dict[str, Any]:
        """Return the fake JSON payload for request stubbing."""

        return self._payload


def test_validate_us_zip_accepts_places_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return success when Places predictions include the requested ZIP.

    Args:
        monkeypatch: Pytest fixture used to stub ``requests.get``.

    Returns:
        None. Asserts expected validation output.

    External dependencies:
        * Stubs :func:`requests.get` used by
          :func:`app.quote.zip_validation._validate_zip_with_places`.
    """

    zip_validation._validate_zip_with_places.cache_clear()

    def fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            {
                "status": "OK",
                "predictions": [
                    {
                        "structured_formatting": {
                            "main_text": "64101",
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr(zip_validation.requests, "get", fake_get)
    assert zip_validation.validate_us_zip("64101", api_key="test-key") == (True, "ok")


def test_validate_us_zip_rejects_unknown_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return not found when Places has no matching prediction.

    Args:
        monkeypatch: Pytest fixture used to stub ``requests.get``.

    Returns:
        None. Asserts expected validation output.

    External dependencies:
        * Stubs :func:`requests.get` used by
          :func:`app.quote.zip_validation._validate_zip_with_places`.
    """

    zip_validation._validate_zip_with_places.cache_clear()

    def fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse({"status": "ZERO_RESULTS", "predictions": []})

    monkeypatch.setattr(zip_validation.requests, "get", fake_get)
    assert zip_validation.validate_us_zip("99999", api_key="test-key") == (
        False,
        "not_found",
    )


def test_validate_us_zip_falls_back_to_format_without_api_key() -> None:
    """Use format-only validation when a Places API key is unavailable.

    Args:
        None.

    Returns:
        None. Asserts expected fallback behavior.

    External dependencies:
        * Calls :func:`app.quote.zip_validation.validate_us_zip`, which resolves
          API keys from Flask config and environment variables.
    """

    zip_validation._validate_zip_with_places.cache_clear()

    assert zip_validation.validate_us_zip("64101", api_key="") == (True, "format_only")
    assert zip_validation.validate_us_zip("64", api_key="") == (False, "invalid_format")
