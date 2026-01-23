"""Hotshot (expedited truck) quote calculations."""

from app.quote.distance import get_distance_miles

from typing import Any, Callable, Dict

from app.services.hotshot_rates import (
    get_current_hotshot_rate,
    get_hotshot_zone_by_miles,
)
from app.services.rate_sets import DEFAULT_RATE_SET, _call_with_rate_set

ZONE_X_PER_LB_RATE = 5.1
ZONE_X_PER_MILE_RATE = 5.2


def calculate_hotshot_quote(
    origin: str,
    destination: str,
    weight: float,
    accessorial_total: float,
    zone_lookup: Callable[[float, str], str] = get_hotshot_zone_by_miles,
    rate_lookup: Callable[[str, str], Any] = get_current_hotshot_rate,
    *,
    rate_set: str = DEFAULT_RATE_SET,
) -> Dict[str, Any]:
    """Calculate hotshot pricing based on distance and database rate tables.

    Args:
        origin: Origin ZIP code.
        destination: Destination ZIP code.
        weight: Shipment weight in pounds.
        accessorial_total: Dollar amount for accessorial charges.
        zone_lookup: Callback to resolve miles to a hotshot zone. Defaults to
            :func:`services.hotshot_rates.get_hotshot_zone_by_miles`.
        rate_lookup: Callback to fetch a :class:`HotshotRate` for a zone. Defaults
            to :func:`services.hotshot_rates.get_current_hotshot_rate`.
        rate_set: Named rate table to evaluate.

    Returns:
        A dictionary with keys ``zone``, ``miles``, ``quote_total``,
        ``weight_break``, ``per_lb``, ``per_mile`` and ``min_charge``.
        ``weight_break`` may be ``None`` when not defined. Zones ``A`` through
        ``J`` charge solely by weight with a minimum charge. Zone ``X``
        overrides the database values and charges ``5.1`` USD per pound with a
        mileage-based minimum of ``(miles * 5.2)`` before fuel and accessorial
        charges.
    """
    miles = get_distance_miles(origin, destination) or 0

    zone = _call_with_rate_set(zone_lookup, rate_set, miles)
    rate = _call_with_rate_set(rate_lookup, rate_set, zone)

    per_lb = float(rate.per_lb)
    fuel_pct = float(rate.fuel_pct)
    weight_break = float(rate.weight_break) if rate.weight_break is not None else None

    if zone.upper() == "X":
        per_lb = ZONE_X_PER_LB_RATE
        per_mile = ZONE_X_PER_MILE_RATE
        min_charge = miles * per_mile
        base = max(min_charge, weight * per_lb)
    else:
        per_mile = None
        min_charge = float(rate.min_charge)
        base = max(min_charge, weight * per_lb)
    subtotal = base * (1 + fuel_pct) + accessorial_total

    return {
        "zone": zone,
        "miles": miles,
        "quote_total": subtotal,
        "weight_break": weight_break,
        "per_lb": per_lb,
        "per_mile": per_mile,
        "min_charge": min_charge,
    }
