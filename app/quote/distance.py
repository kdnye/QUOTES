"""Utilities for resolving road distance using the Google Directions API.

The helpers here fetch an API key from either Flask configuration or the
environment and provide retry-capable HTTP requests. Two public functions
are exposed:

``get_distance_miles`` – simple float result
``get_distance_miles_ex`` – detailed diagnostic dictionary
"""

from __future__ import annotations

import os
from urllib.parse import quote as urlquote

import requests
from flask import current_app, has_app_context
from typing import Optional, Union


# ---- Config helpers ---------------------------------------------------------


def _get_api_key() -> Optional[str]:
    """Resolve Google Maps API key.
    Order of precedence:
      1) Flask app config: GOOGLE_MAPS_API_KEY or GOOGLE_API_KEY
      2) Environment variable: GOOGLE_MAPS_API_KEY
    """
    if has_app_context():
        cfg = getattr(current_app, "config", {})
        key = cfg.get("GOOGLE_MAPS_API_KEY") or cfg.get("GOOGLE_API_KEY")
        if key:
            return key
    return os.getenv("GOOGLE_MAPS_API_KEY")


# ---- Utilities --------------------------------------------------------------


def _sanitize_zip(z: Optional[Union[str, int]]) -> Optional[str]:
    """Return a ``"ZIP,USA"`` string for 5-digit or ZIP+4 inputs.

    Google Directions expects a 5-digit postal code. The external quote forms
    often accept ZIP+4 values such as ``"12345-6789"``. When a 9-digit string
    is provided the previous implementation returned ``None`` which caused the
    hotshot mileage lookup to fail. Truncating to the first five digits preserves
    routing accuracy while allowing users to paste full ZIP+4 codes from other
    systems.
    """

    if not z:
        return None
    s = "".join(ch for ch in str(z).strip() if ch.isdigit())
    if len(s) >= 5:
        first_five = s[:5]
        return f"{first_five},USA"  # disambiguate for Directions API
    return None


def _log(msg: str):
    """Log messages to Flask's logger when available, else print."""
    if has_app_context() and current_app:
        try:
            current_app.logger.info(msg)
            return
        except Exception:
            pass
    # Fallback for CLI/tests
    print(msg)


# ---- HTTP session with retries ---------------------------------------------


def _session_with_retries(total: int = 2) -> requests.Session:
    """Create a ``requests`` session with basic retry behavior."""
    s = requests.Session()
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=total,
            backoff_factor=0.3,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://", HTTPAdapter(max_retries=retry))
    except Exception:
        pass
    return s


# ---- Public API -------------------------------------------------------------


def get_distance_miles(origin_zip, destination_zip):
    """Return driving distance in miles between two 5-digit ZIPs using
    Google Directions API. Returns ``None`` on failure.
    """
    res = get_distance_miles_ex(origin_zip, destination_zip)
    if res["ok"]:
        return res["miles"]
    return None


def get_distance_miles_ex(origin_zip, destination_zip) -> dict:
    """Detailed variant returning diagnostics.

    Returns dict with:
      ok: bool
      miles: float | None
      status: str | None (Google API status)
      error: str | None (local/remote error message)
      url: str (requested URL sans key)
    """
    api_key = _get_api_key()
    if not api_key:
        _log("[distance] No GOOGLE_MAPS_API_KEY found")
        return {
            "ok": False,
            "miles": None,
            "status": None,
            "error": "missing_api_key",
            "url": "",
        }

    o = _sanitize_zip(origin_zip)
    d = _sanitize_zip(destination_zip)
    if not o or not d:
        msg = f"bad_zip origin={origin_zip!r} dest={destination_zip!r}"
        _log(f"[distance] {msg}")
        return {"ok": False, "miles": None, "status": None, "error": msg, "url": ""}

    base = "https://maps.googleapis.com/maps/api/directions/json"
    # URL-encode components to be safe
    url = f"{base}?origin={urlquote(o)}&destination={urlquote(d)}&mode=driving&key={urlquote(api_key)}"
    url_public = url.replace(api_key, "<redacted>")

    try:
        s = _session_with_retries()
        r = s.get(url, timeout=20)
        data = r.json()
        status = data.get("status")
        if status == "OK":
            meters = data["routes"][0]["legs"][0]["distance"]["value"]
            miles = meters / 1609.344
            return {
                "ok": True,
                "miles": miles,
                "status": status,
                "error": None,
                "url": url_public,
            }
        else:
            err = data.get("error_message") or status or "unknown_error"
            _log(f"[distance] status={status} error={err}")
            return {
                "ok": False,
                "miles": None,
                "status": status,
                "error": err,
                "url": url_public,
            }
    except Exception as e:
        _log(f"[distance] exception={e}")
        return {
            "ok": False,
            "miles": None,
            "status": None,
            "error": str(e),
            "url": url_public,
        }
