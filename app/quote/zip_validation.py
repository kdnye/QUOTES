"""ZIP-code validation helpers backed by the Google Places API.

This module focuses on validating U.S. 5-digit ZIP codes by querying the
Google Places Autocomplete endpoint with ``types=postal_code``. The helper is
used by the "Create New Quote" workflow to reject ZIP values that are not
recognized by Google Places.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional, Tuple

import requests
from flask import current_app, has_app_context


def _resolve_maps_api_key() -> str:
    """Return the configured Google Maps API key, if any.

    Args:
        None.

    Returns:
        The first non-empty key from Flask config or environment variables,
        otherwise an empty string.

    External dependencies:
        * Reads ``flask.current_app.config`` when an app context exists.
        * Reads process environment variables via :func:`os.getenv`.
    """

    if has_app_context():
        configured_key = current_app.config.get("GOOGLE_MAPS_API_KEY")
        if configured_key:
            return str(configured_key)
    return os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("MAPS_API_KEY") or ""


def _sanitize_zip(zip_code: str) -> str:
    """Normalize arbitrary ZIP input to its first five numeric digits.

    Args:
        zip_code: Raw ZIP code text submitted by a user.

    Returns:
        A five-digit ZIP string when enough digits are present; otherwise an
        empty string.
    """

    digits = "".join(char for char in str(zip_code or "") if char.isdigit())
    return digits[:5] if len(digits) >= 5 else ""


@lru_cache(maxsize=1024)
def _validate_zip_with_places(zip_code: str, api_key: str) -> Tuple[bool, str]:
    """Validate a ZIP code using Google Places Autocomplete.

    Args:
        zip_code: Candidate ZIP code to validate.
        api_key: Google Maps API key used for Places requests.

    Returns:
        A tuple ``(is_valid, reason)`` where ``reason`` is one of
        ``"ok"``, ``"invalid_format"``, ``"missing_api_key"``,
        ``"places_error"``, or ``"not_found"``.

    External dependencies:
        * Calls ``https://maps.googleapis.com/maps/api/place/autocomplete/json``
          using :mod:`requests`.
    """

    normalized_zip = _sanitize_zip(zip_code)
    if not normalized_zip:
        return False, "invalid_format"
    if not api_key:
        return False, "missing_api_key"

    response = requests.get(
        "https://maps.googleapis.com/maps/api/place/autocomplete/json",
        params={
            "input": normalized_zip,
            "types": "postal_code",
            "components": "country:us",
            "key": api_key,
        },
        timeout=10,
    )
    payload = response.json() if response.ok else {}
    status = payload.get("status")
    if status not in {"OK", "ZERO_RESULTS"}:
        return False, "places_error"

    for prediction in payload.get("predictions", []):
        candidate = prediction.get("structured_formatting", {}).get("main_text", "")
        if _sanitize_zip(candidate) == normalized_zip:
            return True, "ok"
    return False, "not_found"


def validate_us_zip(
    zip_code: str, *, api_key: Optional[str] = None
) -> Tuple[bool, str]:
    """Validate a U.S. ZIP for quote entry.

    Args:
        zip_code: Raw ZIP submitted on the quote form.
        api_key: Optional explicit Google Maps key. When omitted, this helper
            resolves the key from Flask config/environment.

    Returns:
        Tuple ``(is_valid, reason)``. If Google credentials are unavailable,
        validation gracefully falls back to format-only checks.

    External dependencies:
        * Calls :func:`_validate_zip_with_places`, which uses Google Places
          Autocomplete via :mod:`requests`.
    """

    resolved_key = api_key if api_key is not None else _resolve_maps_api_key()
    is_valid, reason = _validate_zip_with_places(zip_code, resolved_key)
    if reason == "missing_api_key":
        return bool(_sanitize_zip(zip_code)), "format_only"
    return is_valid, reason
