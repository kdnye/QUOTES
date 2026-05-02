"""Air freight quote calculations using database rate tables."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from sqlalchemy.exc import OperationalError

from app.database import Session, ZipZone, CostZone, AirCostZone, BeyondRate
from app.services.rate_sets import (
    DEFAULT_RATE_SET,
    _call_with_rate_set,
    normalize_rate_set,
)

BASE_SURCHARGE_PCT = 0.315


def get_dynamic_vsc_pct(*, base: float, orig_zone: str, dest_zone: str, rate_set: str) -> float:
    """Return zone-averaged VSC percentage for air quotes.

    Looks up the EIA regional diesel surcharge for both the origin and destination
    zones and returns their average, so the applied rate reflects the full route
    rather than a single region.

    Args:
        base: Pre-surcharge base freight amount (reserved for future tier logic).
        orig_zone: Origin zone string from ``ZipZone.dest_zone``.
        dest_zone: Destination zone string from ``ZipZone.dest_zone``.
        rate_set: Active named rate table context (reserved for future use).

    Returns:
        Average of origin and destination VSC percentages as a decimal fraction.
        Returns ``0.0`` when either lookup fails. ``get_vsc_pct_for_zone``
        returns ``0.0`` as its universal failure sentinel and no legitimate
        matrix tier produces ``0.0`` (floor is 16%), so a partial result would
        silently halve the surcharge — failing safe is preferable.

    External dependencies:
        Calls ``app.services.fuel_surcharge.get_vsc_pct_for_zone`` for each zone.
    """
    from app.services.fuel_surcharge import get_vsc_pct_for_zone

    _ = (base, rate_set)
    origin_pct = get_vsc_pct_for_zone(orig_zone)
    dest_pct = get_vsc_pct_for_zone(dest_zone)
    if origin_pct == 0.0 or dest_pct == 0.0:
        return 0.0
    return (origin_pct + dest_pct) / 2


def get_zip_zone(
    zipcode: str, *, rate_set: str = DEFAULT_RATE_SET
) -> Optional[ZipZone]:
    """Return the :class:`db.ZipZone` record for a given ZIP code.

    Returns ``None`` if the table is missing or the lookup fails.
    """
    try:
        normalized_rate_set = normalize_rate_set(rate_set)
        with Session() as db:
            record = (
                db.query(ZipZone)
                .filter_by(zipcode=str(zipcode), rate_set=normalized_rate_set)
                .first()
            )
            if record or normalized_rate_set == DEFAULT_RATE_SET:
                return record
            return (
                db.query(ZipZone)
                .filter_by(zipcode=str(zipcode), rate_set=DEFAULT_RATE_SET)
                .first()
            )
    except OperationalError:
        return None


def get_cost_zone(
    concat: str, *, rate_set: str = DEFAULT_RATE_SET
) -> Optional[CostZone]:
    """Return the :class:`db.CostZone` mapping for concatenated origin/dest zones.

    Returns ``None`` if the table is missing or the lookup fails.
    """
    try:
        normalized_rate_set = normalize_rate_set(rate_set)
        with Session() as db:
            record = (
                db.query(CostZone)
                .filter_by(concat=str(concat), rate_set=normalized_rate_set)
                .first()
            )
            if record or normalized_rate_set == DEFAULT_RATE_SET:
                return record
            return (
                db.query(CostZone)
                .filter_by(concat=str(concat), rate_set=DEFAULT_RATE_SET)
                .first()
            )
    except OperationalError:
        return None


def get_air_cost_zone(
    zone: str, *, rate_set: str = DEFAULT_RATE_SET
) -> Optional[AirCostZone]:
    """Return the :class:`db.AirCostZone` record for a given cost zone.

    Returns ``None`` if the table is missing or the lookup fails.
    """
    try:
        normalized_rate_set = normalize_rate_set(rate_set)
        with Session() as db:
            record = (
                db.query(AirCostZone)
                .filter_by(zone=str(zone), rate_set=normalized_rate_set)
                .first()
            )
            if record or normalized_rate_set == DEFAULT_RATE_SET:
                return record
            return (
                db.query(AirCostZone)
                .filter_by(zone=str(zone), rate_set=DEFAULT_RATE_SET)
                .first()
            )
    except OperationalError:
        return None


def get_beyond_rate(zone: Optional[str], *, rate_set: str = DEFAULT_RATE_SET) -> float:
    """Return the beyond charge for a given zone code.

    Returns ``0.0`` if the table is missing, the lookup fails, or ``zone`` is
    falsy.
    """
    if not zone:
        return 0.0
    try:
        normalized_rate_set = normalize_rate_set(rate_set)
        with Session() as db:
            record = (
                db.query(BeyondRate)
                .filter_by(zone=str(zone), rate_set=normalized_rate_set)
                .first()
            )
            if record is None and normalized_rate_set != DEFAULT_RATE_SET:
                record = (
                    db.query(BeyondRate)
                    .filter_by(zone=str(zone), rate_set=DEFAULT_RATE_SET)
                    .first()
                )
            return float(record.rate) if record else 0.0
    except OperationalError:
        return 0.0


def calculate_air_quote(
    origin: str,
    destination: str,
    weight: float,
    accessorial_total: float,
    zip_lookup: Callable[[str, str], Optional[ZipZone]] = get_zip_zone,
    cost_zone_lookup: Callable[[str, str], Optional[CostZone]] = get_cost_zone,
    air_cost_lookup: Callable[[str, str], Optional[AirCostZone]] = get_air_cost_zone,
    beyond_rate_lookup: Callable[[Optional[str], str], float] = get_beyond_rate,
    dynamic_vsc_lookup: Callable[..., float] = get_dynamic_vsc_pct,
    *,
    rate_set: str = DEFAULT_RATE_SET,
) -> Dict[str, Any]:
    """Compute an air quote using rate tables stored in the database.

    If a cost zone mapping is missing for the origin/destination pair, the
    lookup is retried with the zones reversed. This allows tables that only
    define one direction of a route to still resolve correctly.

    Fuel surcharge policy:
        Air quotes apply surcharge logic to base freight only using
        ``BASE_SURCHARGE_PCT`` plus dynamic VSC. Surcharge is not applied to
        beyond charges or accessorials.

    Parameters
    ----------
    origin : str
        Origin ZIP code used for the lookup.
    destination : str
        Destination ZIP code used for the lookup.
    weight : float
        Total shipment weight in pounds.
    accessorial_total : float
        Sum of any additional charges to be applied.
    zip_lookup : Callable[[str], Optional[ZipZone]]
        Lookup function for retrieving :class:`db.ZipZone` records.
    cost_zone_lookup : Callable[[str], Optional[CostZone]]
        Function retrieving :class:`db.CostZone` mappings.
    air_cost_lookup : Callable[[str], Optional[AirCostZone]]
        Function retrieving :class:`db.AirCostZone` rate records.
    beyond_rate_lookup : Callable[[Optional[str]], float]
        Function retrieving beyond charges from :class:`db.BeyondRate`.

    Returns
    -------
    Dict[str, Any]
        Quote details or an error structure when validation fails.
    """

    normalized_rate_set = normalize_rate_set(rate_set)
    surcharge_policy = "base_plus_dynamic_vsc"
    surcharge_reason = (
        "Air quotes apply base freight surcharge (31.5%) plus dynamic VSC."
    )

    def _error_result(msg: str) -> Dict[str, Any]:
        return {
            "zone": None,
            "quote_total": 0,
            "min_charge": None,
            "per_lb": None,
            "weight_break": None,
            "origin_beyond": None,
            "dest_beyond": None,
            "origin_charge": 0,
            "dest_charge": 0,
            "beyond_total": 0,
            "base_rate": 0,
            "fuel_surcharge_base_pct": 0.0,
            "fuel_surcharge_base_amount": 0.0,
            "vsc_pct": 0.0,
            "vsc_amount": 0.0,
            "total_fsc_applied": 0.0,
            "surcharge_applies": True,
            "surcharge_policy": surcharge_policy,
            "surcharge_reason": surcharge_reason,
            "error": msg,
        }

    origin_row = _call_with_rate_set(zip_lookup, normalized_rate_set, str(origin))
    if origin_row is None:
        return _error_result(f"Origin ZIP code {origin} not found")
    if not hasattr(origin_row, "dest_zone") or origin_row.dest_zone is None:
        return _error_result(f"Origin ZIP code {origin} missing dest_zone")
    if not hasattr(origin_row, "beyond"):
        return _error_result(f"Origin ZIP code {origin} missing beyond")

    dest_row = _call_with_rate_set(zip_lookup, normalized_rate_set, str(destination))
    if dest_row is None:
        return _error_result(f"Destination ZIP code {destination} not found")
    if not hasattr(dest_row, "dest_zone") or dest_row.dest_zone is None:
        return _error_result(f"Destination ZIP code {destination} missing dest_zone")
    if not hasattr(dest_row, "beyond"):
        return _error_result(f"Destination ZIP code {destination} missing beyond")

    orig_zone = int(origin_row.dest_zone)
    dest_zone = int(dest_row.dest_zone)
    concat = f"{orig_zone}{dest_zone}"

    cost_zone_row = _call_with_rate_set(cost_zone_lookup, normalized_rate_set, concat)
    if cost_zone_row is None:
        reverse_concat = f"{dest_zone}{orig_zone}"
        cost_zone_row = _call_with_rate_set(
            cost_zone_lookup, normalized_rate_set, reverse_concat
        )
        if cost_zone_row is None:
            return _error_result(
                f"Cost zone not found for concatenated zone {concat} or {reverse_concat}"
            )
    cost_zone = cost_zone_row.cost_zone

    air_cost_row = _call_with_rate_set(air_cost_lookup, normalized_rate_set, cost_zone)
    if air_cost_row is None:
        return _error_result(f"Air cost zone {cost_zone} not found")

    min_charge = float(air_cost_row.min_charge)
    per_lb = float(air_cost_row.per_lb)
    weight_break = float(air_cost_row.weight_break)

    if weight > weight_break:
        base = ((weight - weight_break) * per_lb) + min_charge
    else:
        base = min_charge

    def _parse_beyond(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        val = str(value).strip().upper()
        if val in ("", "N/A", "NO", "NONE", "NAN"):
            return None
        return val.split()[-1]

    origin_beyond = _parse_beyond(origin_row.beyond)
    dest_beyond = _parse_beyond(dest_row.beyond)

    origin_charge = _call_with_rate_set(
        beyond_rate_lookup, normalized_rate_set, origin_beyond
    )
    dest_charge = _call_with_rate_set(
        beyond_rate_lookup, normalized_rate_set, dest_beyond
    )
    beyond_total = origin_charge + dest_charge

    dynamic_vsc_pct = _call_with_rate_set(
        dynamic_vsc_lookup,
        normalized_rate_set,
        base=base,
        orig_zone=str(orig_zone),
        dest_zone=str(dest_zone),
    )
    base_surcharge_amount = base * BASE_SURCHARGE_PCT
    vsc_amount = base * dynamic_vsc_pct
    total_fsc_applied = BASE_SURCHARGE_PCT + dynamic_vsc_pct
    quote_total = (
        base
        + base_surcharge_amount
        + vsc_amount
        + accessorial_total
        + beyond_total
    )

    return {
        "zone": concat,
        "quote_total": quote_total,
        "min_charge": min_charge,
        "per_lb": per_lb,
        "weight_break": weight_break,
        "origin_beyond": origin_beyond,
        "dest_beyond": dest_beyond,
        "origin_charge": origin_charge,
        "dest_charge": dest_charge,
        "beyond_total": beyond_total,
        "base_rate": base,
        "fuel_surcharge_base_pct": BASE_SURCHARGE_PCT,
        "fuel_surcharge_base_amount": base_surcharge_amount,
        "vsc_pct": dynamic_vsc_pct,
        "vsc_amount": vsc_amount,
        "total_fsc_applied": total_fsc_applied,
        "surcharge_applies": True,
        "surcharge_policy": surcharge_policy,
        "surcharge_reason": surcharge_reason,
        "error": None,
    }
