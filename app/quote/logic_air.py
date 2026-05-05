"""Air freight quote calculations using database rate tables."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from sqlalchemy.exc import OperationalError

from app.database import Session, ZipZone, CostZone, AirCostZone, BeyondRate, VscZone
from app.services.rate_sets import (
    DEFAULT_RATE_SET,
    _call_with_rate_set,
    normalize_rate_set,
)



def get_dynamic_vsc_pct(*, zone: str, rate_set: str) -> float:
    """Return dynamic variable surcharge percentage for air quotes.

    Args:
        zone: Zone code used to select a PADD diesel region.
        rate_set: Active named rate table context.

    Returns:
        Percentage expressed as a decimal fraction.

    External dependencies:
        Calls ``app.services.fuel_surcharge.get_vsc_pct_for_zone`` to read the
        active variable surcharge for the provided zone.
    """
    from app.services.fuel_surcharge import get_vsc_pct_for_zone

    _ = rate_set
    return get_vsc_pct_for_zone(str(zone))


def _normalize_zip_lookup_key(zipcode: str) -> Optional[str]:
    """Normalize ZIP input to a 5-digit lookup key.

    Inputs:
        zipcode: Raw ZIP/postal input from quote requests.

    Outputs:
        5-digit ZIP string when valid, otherwise ``None``.

    External dependencies:
        * None.
    """

    normalized = str(zipcode or "").strip()
    if len(normalized) != 5 or not normalized.isdigit():
        return None
    return normalized


def get_vsc_zone_for_zip(zipcode: str, *, rate_set: str = DEFAULT_RATE_SET) -> Optional[int]:
    """Resolve a VSC zone from :class:`app.models.VscZone` for the given ZIP.

    Returns ``None`` when the table is missing, the record is absent, or the
    stored ``vsc_zone`` value is invalid.
    """

    lookup_key = _normalize_zip_lookup_key(zipcode)
    if lookup_key is None:
        return None

    try:
        normalized_rate_set = normalize_rate_set(rate_set)
        with Session() as db:
            record = (
                db.query(VscZone)
                .filter_by(zipcode=lookup_key, rate_set=normalized_rate_set)
                .first()
            )
            if record is None and normalized_rate_set != DEFAULT_RATE_SET:
                record = (
                    db.query(VscZone)
                    .filter_by(zipcode=lookup_key, rate_set=DEFAULT_RATE_SET)
                    .first()
                )
            if record is None or record.vsc_zone is None:
                return None
            if not 1 <= int(record.vsc_zone) <= 10:
                return None
            return int(record.vsc_zone)
    except (OperationalError, ValueError, TypeError):
        return None


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
    vsc_zone_lookup: Callable[[str, str], Optional[int]] = get_vsc_zone_for_zip,
    *,
    rate_set: str = DEFAULT_RATE_SET,
) -> Dict[str, Any]:
    """Compute an air quote using rate tables stored in the database.

    If a cost zone mapping is missing for the origin/destination pair, the
    lookup is retried with the zones reversed. This allows tables that only
    define one direction of a route to still resolve correctly.

    Fuel surcharge policy:
        Air quotes apply a single FSC derived from the origin zone's current
        EIA diesel price. The FSC is applied to total base freight (linehaul
        plus any beyond charges). Accessorials are not surcharged.

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
    surcharge_policy = "origin_zone_fsc"
    surcharge_reason = (
        "Air quotes apply a single fuel surcharge from the origin zone's EIA diesel price."
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

    origin_vsc_zone = _call_with_rate_set(vsc_zone_lookup, normalized_rate_set, str(origin))
    if origin_vsc_zone is None:
        return _error_result(f"Origin ZIP code {origin} missing valid vsc_zone")

    dest_vsc_zone = _call_with_rate_set(vsc_zone_lookup, normalized_rate_set, str(destination))
    if dest_vsc_zone is None:
        return _error_result(
            f"Destination ZIP code {destination} missing valid vsc_zone"
        )

    origin_vsc_pct = _call_with_rate_set(
        dynamic_vsc_lookup, normalized_rate_set, zone=str(origin_vsc_zone)
    )
    dest_vsc_pct = _call_with_rate_set(
        dynamic_vsc_lookup, normalized_rate_set, zone=str(dest_vsc_zone)
    )

    total_base_freight = base + beyond_total
    fsc_pct = origin_vsc_pct
    fsc_amount = total_base_freight * fsc_pct
    total_fsc_applied = fsc_pct
    quote_total = total_base_freight + fsc_amount + accessorial_total

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
        "fuel_surcharge_base_pct": 0.0,
        "fuel_surcharge_base_amount": 0.0,
        "vsc_pct": fsc_pct,
        "origin_vsc_pct": origin_vsc_pct,
        "dest_vsc_pct": dest_vsc_pct,
        "vsc_amount": fsc_amount,
        "total_fsc_applied": total_fsc_applied,
        "surcharge_applies": True,
        "surcharge_policy": surcharge_policy,
        "surcharge_reason": surcharge_reason,
        "error": None,
    }
