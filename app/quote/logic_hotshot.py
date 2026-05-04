"""Hotshot (expedited truck) quote calculations."""

from typing import Any, Callable, Dict, Optional

from app.quote.distance import get_distance_miles
from app.quote.logic_air import get_zip_zone
from app.services.hotshot_rates import (
    get_current_hotshot_rate,
    get_hotshot_zone_by_miles,
)
from app.services.rate_sets import DEFAULT_RATE_SET, _call_with_rate_set

ZONE_X_PER_LB_RATE = 5.1
ZONE_X_PER_MILE_RATE = 5.2
BASE_SURCHARGE_PCT = 0.0


def _resolve_destination_zone(
    destination: str,
    *,
    rate_set: str,
    zip_lookup: Callable[[str, str], Optional[Any]],
) -> tuple[str, list[Dict[str, str]]]:
    """Resolve destination ZIP to a destination zone string for VSC inputs.

    Args:
        destination: Destination ZIP code string.
        rate_set: Named rate set context for ZIP lookup.
        zip_lookup: Function that calls ``app.quote.logic_air.get_zip_zone`` style
            lookups to return a ZIP-zone row object with ``dest_zone``.

    Returns:
        A tuple of ``(dest_zone, warning_metadata)``. ``dest_zone`` is the
        resolved zone string or ``"NATIONAL"`` when lookup fails.

    External dependencies:
        Calls ``app.quote.logic_air.get_zip_zone`` (via ``zip_lookup``) to
        read persisted ZIP-to-zone data.
    """

    zip_row = _call_with_rate_set(zip_lookup, rate_set, str(destination))
    if zip_row is not None and getattr(zip_row, "dest_zone", None) is not None:
        return str(zip_row.dest_zone), []

    return "NATIONAL", [
        {
            "code": "HOTSHOT_DEST_ZONE_FALLBACK",
            "message": (
                "Destination zone lookup failed for hotshot quote; "
                "used NATIONAL fallback for VSC context."
            ),
            "destination_zip": str(destination),
        }
    ]


def get_dynamic_vsc_pct(
    *, base: float, miles: float, zone: str, dest_zone: str, rate_set: str
) -> float:
    """Return dynamic variable surcharge percentage for hotshot quotes.

    Args:
        base: The computed pre-surcharge linehaul amount.
        miles: Route mileage used for the quote.
        zone: Hotshot zone code resolved from mileage.
        dest_zone: Destination zone code used to select a PADD diesel region.
        rate_set: Active named rate table context.

    Returns:
        Percentage expressed as a decimal fraction (for example ``0.35`` for
        35%).  Reads the current EIA diesel price from the ``FuelSurcharge``
        table and maps it to a surcharge via the ``vsc_matrix`` AppSetting.
        Returns ``0.0`` when the DB or config is unavailable.

    External dependencies:
        * ``app.services.fuel_surcharge.get_vsc_pct_for_zone`` — queries the
          ``FuelSurcharge`` table and ``vsc_zones``/``vsc_matrix`` AppSettings.
    """

    from app.services.fuel_surcharge import get_vsc_pct_for_zone

    _ = (base, miles, zone, rate_set)
    return get_vsc_pct_for_zone(dest_zone)


def calculate_hotshot_quote(
    origin: str,
    destination: str,
    weight: float,
    accessorial_total: float,
    zone_lookup: Callable[[float, str], str] = get_hotshot_zone_by_miles,
    rate_lookup: Callable[[str, str], Any] = get_current_hotshot_rate,
    zip_lookup: Callable[[str, str], Optional[Any]] = get_zip_zone,
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
        zip_lookup: Callback to resolve ZIP rows from the existing ZIP zone
            table flow in :func:`app.quote.logic_air.get_zip_zone`.

    Returns:
        A dictionary with keys ``zone``, ``miles``, ``quote_total``,
        ``weight_break``, ``per_lb``, ``per_mile`` and ``min_charge``.
        ``weight_break`` may be ``None`` when not defined. Zones ``A`` through
        ``J`` charge solely by weight with a minimum charge. Zone ``X``
        overrides the database values and charges ``5.1`` USD per pound with a
        mileage-based minimum of ``(miles * 5.2)`` before surcharge and
        accessorial charges.

    Compatibility note:
        ``HotshotRate.fuel_pct`` is temporarily ignored for hotshot totals when
        dynamic surcharge mode is enabled; surcharge is computed from
        ``BASE_SURCHARGE_PCT`` plus dynamic VSC.
    """
    miles = get_distance_miles(origin, destination) or 0

    zone = _call_with_rate_set(zone_lookup, rate_set, miles)
    rate = _call_with_rate_set(rate_lookup, rate_set, zone)

    per_lb = float(rate.per_lb)
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

    dest_zone, warning_metadata = _resolve_destination_zone(
        destination,
        rate_set=rate_set,
        zip_lookup=zip_lookup,
    )

    dynamic_vsc_pct = get_dynamic_vsc_pct(
        base=base,
        miles=miles,
        zone=zone,
        dest_zone=dest_zone,
        rate_set=rate_set,
    )
    base_surcharge_amount = base * BASE_SURCHARGE_PCT
    vsc_amount = base * dynamic_vsc_pct
    total_fsc_applied = BASE_SURCHARGE_PCT + dynamic_vsc_pct
    quote_total = base + base_surcharge_amount + vsc_amount + accessorial_total

    return {
        "zone": zone,
        "miles": miles,
        "quote_total": quote_total,
        "base_rate": base,
        "fuel_surcharge_base_pct": BASE_SURCHARGE_PCT,
        "fuel_surcharge_base_amount": base_surcharge_amount,
        "vsc_pct": dynamic_vsc_pct,
        "vsc_amount": vsc_amount,
        "total_fsc_applied": total_fsc_applied,
        "weight_break": weight_break,
        "per_lb": per_lb,
        "per_mile": per_mile,
        "min_charge": min_charge,
        "dest_zone": dest_zone,
        "warning_metadata": warning_metadata,
    }
