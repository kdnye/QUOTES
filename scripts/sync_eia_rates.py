"""Synchronize fuel surcharge rates from EIA series into the database.

This script initializes the Flask application via :func:`app.create_app`, fetches
configured EIA series IDs, validates each response, and upserts
:class:`app.models.FuelSurcharge` rows.

Inputs:
    * Environment variables:
        * ``EIA_API_KEY``: Optional EIA API key for higher rate limits.
        * ``EIA_TIMEOUT_SECONDS``: Request timeout in seconds (default: ``15``).
        * ``EIA_SERIES_MAP_JSON``: Optional JSON object mapping region labels to
          EIA series IDs.
        * ``EIA_COMMIT_STRATEGY``: ``all_or_nothing`` (default) or
          ``per_region``.

Outputs:
    * Updated ``fuel_surcharges`` rows with current rate and ``last_updated``.
    * Structured log output for auditing and operator troubleshooting.

External dependencies:
    * Calls the EIA API endpoint at ``https://api.eia.gov/v2/seriesid/{series}``.
    * Uses Flask-SQLAlchemy session handling via :data:`app.models.db.session`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import requests
from requests import Response
from sqlalchemy.exc import SQLAlchemyError

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.models import FuelSurcharge, db

LOGGER = logging.getLogger(__name__)
EIA_BASE_URL = "https://api.eia.gov/v2/seriesid"
DEFAULT_TIMEOUT_SECONDS = 15

# Default mapping can be overridden with EIA_SERIES_MAP_JSON.
DEFAULT_EIA_SERIES_MAP: Dict[str, str] = {
    "USGC": "PET.EMD_EPD2D_PTE_NUS_DPG.W",
    "EMEC": "PET.EMD_EPD2D_PTE_R10_DPG.W",
    "ENEC": "PET.EMD_EPD2D_PTE_R20_DPG.W",
    "WCGC": "PET.EMD_EPD2D_PTE_R30_DPG.W",
    "WCMC": "PET.EMD_EPD2D_PTE_R40_DPG.W",
}


@dataclass
class RegionSyncResult:
    """Outcome of synchronizing one region.

    Inputs:
        region: Internal region label matching ``FuelSurcharge.padd_region``.
        period_date: EIA data period interpreted as a date string.
        rate: Numeric fuel surcharge rate stored for the region.

    Outputs:
        Dataclass instance emitted in logs and summary payloads.

    External dependencies:
        None. This class is a local container for script state.
    """

    region: str
    period_date: str
    rate: float


def load_series_map() -> Dict[str, str]:
    """Return configured EIA series mapping from env or defaults.

    Inputs:
        Reads ``EIA_SERIES_MAP_JSON`` from ``os.environ`` when provided.

    Outputs:
        Dictionary mapping region labels to EIA series IDs.

    External dependencies:
        Uses :func:`json.loads` for structured environment configuration.
    """

    raw = os.environ.get("EIA_SERIES_MAP_JSON")
    if not raw:
        return dict(DEFAULT_EIA_SERIES_MAP)

    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("EIA_SERIES_MAP_JSON must be a non-empty JSON object")

    normalized: Dict[str, str] = {}
    for region, series_id in parsed.items():
        if not isinstance(region, str) or not isinstance(series_id, str):
            raise ValueError("EIA_SERIES_MAP_JSON keys/values must be strings")
        region_clean = region.strip()
        series_clean = series_id.strip()
        if not region_clean or not series_clean:
            raise ValueError("EIA_SERIES_MAP_JSON contains blank region or series ID")
        normalized[region_clean] = series_clean
    return normalized


def fetch_series_payload(series_id: str, timeout_seconds: int) -> Dict[str, Any]:
    """Fetch one EIA series payload with strict HTTP handling.

    Inputs:
        series_id: EIA series identifier to request.
        timeout_seconds: Positive timeout for HTTP request.

    Outputs:
        Parsed JSON dictionary from EIA.

    External dependencies:
        Calls :func:`requests.get` against the EIA v2 API endpoint.
    """

    url = f"{EIA_BASE_URL}/{series_id}"
    params = {"api_key": os.environ.get("EIA_API_KEY", "")}
    response: Response = requests.get(url, params=params, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"EIA payload for {series_id} is not a JSON object")
    return payload


def extract_latest_point(
    payload: Mapping[str, Any], series_id: str
) -> Tuple[str, float]:
    """Validate an EIA payload and return latest period and numeric value.

    Inputs:
        payload: JSON object returned by EIA for a series.
        series_id: Series ID used only for contextual error messages.

    Outputs:
        Tuple of ``(period_date, numeric_rate)`` where ``period_date`` is a
        normalized ``YYYY-MM-DD`` string when possible.

    External dependencies:
        Uses :class:`datetime` parsing for readable period normalization.
    """

    response_obj = payload.get("response")
    if not isinstance(response_obj, dict):
        raise ValueError(f"EIA payload missing 'response' object for {series_id}")

    data = response_obj.get("data")
    if not isinstance(data, list):
        raise ValueError(f"EIA payload missing 'data' list for {series_id}")
    if not data:
        raise ValueError(f"EIA payload data list is empty for {series_id}")

    latest = data[0]
    if not isinstance(latest, dict):
        raise ValueError(f"EIA payload data entry is not an object for {series_id}")

    raw_period = latest.get("period")
    raw_value = latest.get("value")
    if raw_period is None:
        raise ValueError(f"EIA payload entry missing 'period' for {series_id}")
    if raw_value is None:
        raise ValueError(f"EIA payload entry missing 'value' for {series_id}")

    try:
        rate = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"EIA payload value is non-numeric for {series_id}: {raw_value!r}"
        ) from exc

    period_text = str(raw_period)
    normalized_period = period_text
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(period_text, fmt)
            if fmt == "%Y":
                normalized_period = parsed.strftime("%Y-01-01")
            elif fmt == "%Y-%m":
                normalized_period = parsed.strftime("%Y-%m-01")
            else:
                normalized_period = parsed.strftime("%Y-%m-%d")
            break
        except ValueError:
            continue

    return normalized_period, rate


def upsert_region_rate(region: str, rate: float) -> None:
    """Insert or update one ``FuelSurcharge`` row.

    Inputs:
        region: Region label matched against ``FuelSurcharge.padd_region``.
        rate: Numeric rate to persist in ``FuelSurcharge.current_rate``.

    Outputs:
        Mutated SQLAlchemy session with one pending insert or update.

    External dependencies:
        Uses ``db.session`` query/identity map operations from Flask-SQLAlchemy.
    """

    existing = FuelSurcharge.query.filter_by(padd_region=region).first()
    if existing is None:
        db.session.add(FuelSurcharge(padd_region=region, current_rate=rate))
    else:
        existing.current_rate = rate


def sync_eia_rates(commit_strategy: str) -> int:
    """Synchronize all configured regions and return process exit code.

    Inputs:
        commit_strategy: Either ``all_or_nothing`` or ``per_region``.

    Outputs:
        ``0`` for success/partial success, ``1`` when all regions fail.

    External dependencies:
        * Calls :func:`fetch_series_payload` for remote EIA data.
        * Commits/rolls back via :data:`app.models.db.session`.
    """

    series_map = load_series_map()
    timeout_seconds = int(
        os.environ.get("EIA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    )

    successes = 0
    failures = 0

    for region, series_id in series_map.items():
        try:
            payload = fetch_series_payload(series_id, timeout_seconds=timeout_seconds)
            period_date, rate = extract_latest_point(payload, series_id=series_id)
            upsert_region_rate(region, rate)
            if commit_strategy == "per_region":
                db.session.commit()
            successes += 1
            LOGGER.info(
                "synced fuel surcharge: region=%s period=%s rate=%s",
                region,
                period_date,
                rate,
            )
        except (requests.RequestException, ValueError, SQLAlchemyError) as exc:
            failures += 1
            db.session.rollback()
            LOGGER.exception(
                "failed syncing fuel surcharge: region=%s series_id=%s error=%s",
                region,
                series_id,
                exc,
            )

    if commit_strategy == "all_or_nothing" and successes > 0:
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            LOGGER.exception(
                "database commit failed for all_or_nothing strategy: %s", exc
            )
            return 1

    LOGGER.info("sync summary: successes=%s failures=%s", successes, failures)

    if successes == 0:
        LOGGER.error(
            "all configured regions failed during sync",
            extra={"event": "eia_sync_all_regions_failed", "failures": failures},
        )
        return 1

    return 0


def main() -> int:
    """Script entrypoint that initializes Flask app context and runs sync."""

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    commit_strategy = os.environ.get("EIA_COMMIT_STRATEGY", "all_or_nothing").strip()
    if commit_strategy not in {"all_or_nothing", "per_region"}:
        raise ValueError("EIA_COMMIT_STRATEGY must be 'all_or_nothing' or 'per_region'")

    app = create_app()
    with app.app_context():
        return sync_eia_rates(commit_strategy=commit_strategy)


if __name__ == "__main__":
    raise SystemExit(main())
