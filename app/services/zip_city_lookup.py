"""Resolve a 5-digit US ZIP to its ``(city, state)`` pair.

The Science Care quote service uses this to fall back from a missed
ZIP-keyed established lane to a metro-keyed one (mirroring the workbook's
``lab_code + "City,State"`` VLOOKUP). The lookup reads the same
``Zipcode_Zones.csv`` reference the rest of the app already ships - the
file is loaded lazily on first use and cached in-process so the SC quote
hot path doesn't re-parse 29k rows per request.

The DB ``ZipZone`` table only carries zone / beyond / notes today; it does
not store city or state, so the CSV is the source for those fields. If
``ZipZone`` ever grows city columns this helper can swap to the DB.
"""

from __future__ import annotations

import csv
import os
import threading
from pathlib import Path
from typing import Optional

# Lazy cache - populated on first call to :func:`lookup_city_state`. The
# lock guards against two requests racing through the first lookup at
# the same time (unlikely in CPython but cheap insurance).
_INDEX: dict[str, tuple[str, str]] | None = None
_INDEX_LOCK = threading.Lock()

# Env var overrides the default CSV path - tests set this to point at a
# fixture file instead of the production reference.
_PATH_ENV_VAR = "SC_ZIPCODE_ZONES_PATH"

# Default location: the repo's top-level Zipcode_Zones.csv. Computed from
# this module's own location (app/services/zip_city_lookup.py) so the
# helper works whether the app is run from the repo root, via gunicorn,
# or inside a Cloud Run container.
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "Zipcode_Zones.csv"


def _normalize_zip(value: str | int | None) -> str:
    """Return ``value`` as a zero-padded 5-digit string, or empty."""

    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not text:
        return ""
    return text[:5].zfill(5)


def _resolve_path() -> Path:
    override = os.environ.get(_PATH_ENV_VAR)
    return Path(override) if override else _DEFAULT_PATH


def _load_index(path: Path) -> dict[str, tuple[str, str]]:
    """Parse the CSV into ``{zip5: (city_upper, state_upper)}``.

    City and state are uppercased on the way in so the SC fallback can
    compare against admin-supplied lane rows without per-request casing
    work. Rows whose ZIP can't be normalized or whose city/state are
    blank are skipped silently - they wouldn't be matchable anyway.
    """

    index: dict[str, tuple[str, str]] = {}
    if not path.is_file():
        return index
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            zip5 = _normalize_zip(row.get("Zipcode"))
            if not zip5:
                continue
            city = (row.get("City") or "").strip().upper()
            state = (row.get("State") or "").strip().upper()
            if not city or not state:
                continue
            index[zip5] = (city, state)
    return index


def lookup_city_state(zip_code: str | int | None) -> Optional[tuple[str, str]]:
    """Return ``(city, state)`` for ``zip_code`` or ``None`` if not found.

    Both values are uppercased. The CSV is parsed once per process; if
    the file is missing (e.g. trimmed-down test image), every lookup
    returns ``None`` and callers fall back to whatever default they had.
    """

    global _INDEX
    zip5 = _normalize_zip(zip_code)
    if not zip5:
        return None
    if _INDEX is None:
        with _INDEX_LOCK:
            if _INDEX is None:
                _INDEX = _load_index(_resolve_path())
    return _INDEX.get(zip5)


def reset_cache() -> None:
    """Drop the in-process cache. Tests call this after pointing the
    env var at a fixture so the next lookup re-reads the file.
    """

    global _INDEX
    with _INDEX_LOCK:
        _INDEX = None
