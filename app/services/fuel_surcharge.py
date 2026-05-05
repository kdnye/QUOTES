"""Variable fuel surcharge computation from EIA diesel prices and VSC config.

Reads two AppSetting keys:

* ``vsc_zones``: JSON object mapping destination zone codes to PADD region
  labels (e.g. ``{"1": "PADD1", "7": "PADD4"}``).  Zones not listed fall
  back to ``"NATIONAL"``.
* ``vsc_matrix``: JSON array of price tiers that map diesel $/gallon ranges to
  surcharge percentages (e.g.
  ``[{"min": 5.0, "max": 5.5, "pct": 0.35}, ...]``).

Current diesel prices are stored in the ``FuelSurcharge`` table and kept
current by running ``scripts/sync_eia_rates.py`` (or a scheduled job that
calls it).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.exc import SQLAlchemyError

LOGGER = logging.getLogger(__name__)
NATIONAL_REGION = "NATIONAL"
FALLBACK_VSC_PCT = 0.0


def _parse_json_safely(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        LOGGER.warning("Failed to parse VSC JSON config")
        return None


def resolve_padd_region(dest_zone: str, zones_config: Any) -> str:
    """Return the PADD region for *dest_zone* using *zones_config*.

    Falls back to ``NATIONAL`` when the zone is absent from the mapping or
    *zones_config* is not a dict.

    Inputs:
        dest_zone: Destination zone value from ZIP lookup. May be zero-padded
            (for example ``"09"``) depending on source data formatting.
        zones_config: Parsed ``vsc_zones`` mapping of zone codes to PADD names.

    Outputs:
        Region name string such as ``"PADD4"`` when found, else
        :data:`NATIONAL_REGION`.

    External dependencies:
        None.
    """
    if isinstance(zones_config, dict):
        raw_zone = str(dest_zone).strip()
        lookup_candidates = [raw_zone]
        if raw_zone.isdigit():
            lookup_candidates.append(str(int(raw_zone)))

        for candidate in lookup_candidates:
            region = zones_config.get(candidate)
            if region and isinstance(region, str):
                return region.strip()

    return NATIONAL_REGION


def lookup_matrix_pct(diesel_price: float, matrix: Any) -> Optional[float]:
    """Return surcharge percentage (decimal fraction) for *diesel_price*.

    Scans *matrix* for the first tier where ``min <= diesel_price < max`` and
    returns that tier's ``pct`` value.  Returns ``None`` when no tier matches
    or *matrix* is malformed.
    """
    if not isinstance(matrix, list):
        return None
    for tier in matrix:
        if not isinstance(tier, dict):
            continue
        try:
            min_p = float(tier["min"])
            max_p = float(tier["max"])
            pct = float(tier["pct"])
        except (KeyError, TypeError, ValueError):
            continue
        if min_p <= diesel_price < max_p:
            return pct
    return None


def get_vsc_pct_for_zone(dest_zone: str) -> float:
    """Return dynamic VSC percentage for *dest_zone*.

    Workflow:

    1. Read ``vsc_matrix`` AppSetting; return 0.0 if not configured.
    2. Read ``vsc_zones`` AppSetting; map *dest_zone* → PADD region (default
       ``NATIONAL``).
    3. Query ``FuelSurcharge`` for that region; fall back to ``NATIONAL`` when
       the specific region has no row.
    4. Look up the current diesel price in the matrix and return the matching
       surcharge percentage.

    Returns ``0.0`` on any failure so quotes are never blocked by a missing
    config.
    """
    try:
        from app.models import FuelSurcharge
        from app.services.settings import get_settings_cache

        cache = get_settings_cache()
        zones_raw = cache.get("vsc_zones")
        matrix_raw = cache.get("vsc_matrix")

        matrix = _parse_json_safely(matrix_raw.raw_value if matrix_raw else None)
        if matrix is None:
            LOGGER.debug("vsc_matrix not configured; dynamic VSC is 0.0")
            return FALLBACK_VSC_PCT

        zones = _parse_json_safely(zones_raw.raw_value if zones_raw else None)
        region = resolve_padd_region(dest_zone, zones)

        fuel_row = FuelSurcharge.query.filter_by(padd_region=region).first()
        if fuel_row is None and region != NATIONAL_REGION:
            fuel_row = FuelSurcharge.query.filter_by(
                padd_region=NATIONAL_REGION
            ).first()

        if fuel_row is None:
            LOGGER.warning(
                "No FuelSurcharge record for region=%s or NATIONAL; VSC is 0.0",
                region,
            )
            return FALLBACK_VSC_PCT

        pct = lookup_matrix_pct(fuel_row.current_rate, matrix)
        if pct is None:
            LOGGER.warning(
                "Diesel price %.3f not in any vsc_matrix tier; VSC is 0.0",
                fuel_row.current_rate,
            )
            return FALLBACK_VSC_PCT

        LOGGER.debug(
            "VSC resolved: zone=%s region=%s diesel=%.3f pct=%.4f",
            dest_zone,
            region,
            fuel_row.current_rate,
            pct,
        )
        return pct

    except SQLAlchemyError as exc:
        LOGGER.exception("DB error resolving VSC for zone=%s: %s", dest_zone, exc)
        return FALLBACK_VSC_PCT
    except RuntimeError as exc:
        # No Flask application context (e.g. offline unit tests)
        LOGGER.debug("No app context for VSC lookup: %s", exc)
        return FALLBACK_VSC_PCT


__all__ = [
    "FALLBACK_VSC_PCT",
    "NATIONAL_REGION",
    "get_vsc_pct_for_zone",
    "lookup_matrix_pct",
    "resolve_padd_region",
]
