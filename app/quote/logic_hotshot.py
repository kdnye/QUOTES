"""Hotshot (expedited truck) quote calculations."""

import math
import logging
from typing import Any, Callable, Dict, Optional

from app.quote.distance import get_distance_miles
from app.quote.logic_air import get_vsc_zone_for_zip as get_air_vsc_zone_for_zip
from app.quote.logic_air import get_zip_zone
from app.services.hotshot_rates import (
    get_current_hotshot_rate,
    get_hotshot_zone_by_miles,
)
from app.services.rate_sets import DEFAULT_RATE_SET, _call_with_rate_set

LOGGER = logging.getLogger(__name__)


# NYC ZIP whitelist from the FSI VSC-Locked workbook's
# `Domestic Hotshot Quotes!P3:P43`. When the destination ZIP is in this set,
# the workbook's `D18` branch overrides the per-mile / weight-break base
# with the SCPA->NYC local-delivery flat rate ($1,100) before MAX'ing
# against the standard zone path. Accessorials and VSC apply normally on
# top of the override.
NYC_FLAT_RATE_ZIPS: frozenset[str] = frozenset(
    {
        "10001", "10002", "10003", "10004", "10006", "10007", "10010",
        "10012", "10013", "10014", "10016", "10017", "10018", "10019",
        "10021", "10022", "10023", "10024", "10025", "10027", "10028",
        "10029", "10030", "10031", "10032", "10034", "10035", "10036",
        "10038", "10039", "10049", "10065", "10075", "10128", "10168",
        "10461", "11215", "11758",
    }
)
NYC_FLAT_RATE_USD: float = 1100.0


class VscDestinationZoneLookupError(ValueError):
    """Raised when a VSC destination zone cannot be resolved for a ZIP code."""


def get_vsc_zone_for_zip(
    destination_zipcode: str,
    *,
    rate_set: str,
    zip_lookup: Callable[[str, str], Optional[Any]] = get_zip_zone,
    vsc_zone_lookup: Callable[[str, str], Optional[int]] = get_air_vsc_zone_for_zip,
    quote_type: str = "hotshot",
    lookup_source: str = "zip_zone_table",
    raise_on_missing: bool = False,
) -> tuple[str, list[Dict[str, str]]]:
    """Resolve the VSC destination zone from dedicated VSC or ZIP-zone data.

    Inputs:
        destination_zipcode: Destination ZIP code string.
        rate_set: Named rate-set context used by the ZIP lookup layer.
        zip_lookup: Callable using ``app.quote.logic_air.get_zip_zone``-style
            arguments that returns a row with ``dest_zone``.
        vsc_zone_lookup: Callable using
            ``app.quote.logic_air.get_vsc_zone_for_zip``-style arguments that
            returns a numeric ``vsc_zone`` from :class:`app.models.VscZone`.
        quote_type: Quote context included in diagnostic logging.
        lookup_source: Human-readable data source for debug logs.
        raise_on_missing: When ``True``, raise
            :class:`VscDestinationZoneLookupError` if the ZIP has no VSC zone.

    Outputs:
        Tuple of ``(vsc_dest_zone, warning_metadata)``. ``vsc_dest_zone`` is a
        zone string from ZIP-zone data, or ``"NATIONAL"`` deterministic fallback
        when ``raise_on_missing`` is ``False``.

    External dependencies:
        Calls ``app.quote.logic_air.get_zip_zone`` (via ``zip_lookup``) to read
        ZIP-to-zone records that feed VSC region selection.
    """

    resolved_vsc_zone = _call_with_rate_set(
        vsc_zone_lookup, rate_set, str(destination_zipcode)
    )
    if resolved_vsc_zone is not None:
        return str(resolved_vsc_zone), []

    zip_row = _call_with_rate_set(zip_lookup, rate_set, str(destination_zipcode))
    if zip_row is not None and getattr(zip_row, "dest_zone", None) is not None:
        return str(zip_row.dest_zone), []

    context = {
        "quote_type": quote_type,
        "destination_zip": str(destination_zipcode),
        "lookup_source": lookup_source,
        "rate_set": rate_set,
    }
    message = (
        "VSC destination zone lookup failed; destination ZIP is missing from "
        "lookup source."
    )
    LOGGER.warning(message, extra=context)
    if raise_on_missing:
        raise VscDestinationZoneLookupError(
            f"{message} quote_type={quote_type} destination_zip={destination_zipcode} "
            f"lookup_source={lookup_source} rate_set={rate_set}"
        )

    return "NATIONAL", [
        {
            "code": "HOTSHOT_DEST_ZONE_FALLBACK",
            "message": (
                "Destination zone lookup failed for hotshot quote; "
                "used NATIONAL fallback for VSC context."
            ),
            "destination_zip": str(destination_zipcode),
            "lookup_source": lookup_source,
        }
    ]


def _resolve_destination_zone(
    destination: str,
    *,
    rate_set: str,
    zip_lookup: Callable[[str, str], Optional[Any]],
    vsc_zone_lookup: Callable[[str, str], Optional[int]],
) -> tuple[str, list[Dict[str, str]]]:
    """Resolve destination ZIP to a VSC destination zone string.

    Args:
        destination: Destination ZIP code string.
        rate_set: Named rate set context for ZIP lookup.
        zip_lookup: Function that calls ``app.quote.logic_air.get_zip_zone`` style
            lookups to return a ZIP-zone row object with ``dest_zone``.
        vsc_zone_lookup: Function that calls
            ``app.quote.logic_air.get_vsc_zone_for_zip`` style lookups to
            return a dedicated VSC zone when available.

    Returns:
        A tuple of ``(vsc_dest_zone, warning_metadata)``. ``vsc_dest_zone`` is
        from ZIP-zone data used by VSC/PADD mapping, or ``"NATIONAL"`` fallback.

    External dependencies:
        Calls ``app.quote.logic_air.get_zip_zone`` (via ``zip_lookup``) to
        read persisted ZIP-to-zone data.
    """

    return get_vsc_zone_for_zip(
        destination,
        rate_set=rate_set,
        zip_lookup=zip_lookup,
        vsc_zone_lookup=vsc_zone_lookup,
        quote_type="hotshot",
        lookup_source="zip_zone_table",
        raise_on_missing=False,
    )


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
    vsc_zone_lookup: Callable[[str, str], Optional[int]] = get_air_vsc_zone_for_zip,
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

        Zones ``A`` through ``J`` use the FSI VSC-Locked workbook's
        weight-break formula
        ``IF(weight > weight_break, ((weight - weight_break) * per_lb) + min, min)``
        with all three values sourced from the resolved :class:`HotshotRate`
        row.

        Zone ``X`` charges ``miles * per_mile`` (no weight*per_lb floor and
        no fuel surcharge — the FSI workbook's ``D18`` Zone-X branch is pure
        per-mile, and the workbook stores ``Fuel = 0`` for Zone X). The
        row's ``per_mile`` must be non-NULL; a NULL value raises
        :class:`ValueError`.

        NYC override: when ``destination`` is in :data:`NYC_FLAT_RATE_ZIPS`,
        a parallel "NYC base" of :data:`NYC_FLAT_RATE_USD` (no fuel
        surcharge) is computed and the **higher** of (zone-base + fuel)
        and (NYC base) becomes the freight subtotal. This mirrors the
        workbook's ``D20 = MAX(D17, D18)`` line.

    Surcharge policy:
        For zones A-J the rate row's ``fuel_pct`` is applied to the base
        (matches the workbook's hard-coded ``*1.315`` multiplier on
        ``D17``). Zone X and the NYC flat-rate override do NOT apply a
        fuel surcharge (workbook's ``D18`` has no ``*1.315``).

        The dynamic EIA-based VSC is then applied to the post-fuel base
        (excluding accessorials). The VSC zone is the **larger** of the
        origin and destination VSC zones, matching the workbook's
        ``K10 = MAX(K8, K9)``.
    """
    miles = math.ceil(get_distance_miles(origin, destination) or 0)

    zone = _call_with_rate_set(zone_lookup, rate_set, miles)
    rate = _call_with_rate_set(rate_lookup, rate_set, zone)

    per_lb = float(rate.per_lb)
    weight_break = float(rate.weight_break) if rate.weight_break is not None else None

    if zone.upper() == "X":
        if rate.per_mile is None:
            raise ValueError(
                f"Zone X HotshotRate row is missing per_mile "
                f"(rate_set={rate_set!r}, miles={miles}). "
                f"Edit the row in /admin/hotshot_rates and set Per Mile."
            )
        per_mile = float(rate.per_mile)
        # Workbook D18 Zone-X branch: pure miles * per_mile. No per-lb floor,
        # no fuel surcharge (D18 has no *1.315 multiplier; FSI stores Fuel=0
        # for Zone X). Stash the per-mile charge in min_charge for the
        # downstream payload field; base IS that charge.
        min_charge = miles * per_mile
        base = min_charge
    else:
        per_mile = None
        min_charge = float(rate.min_charge)
        # Workbook D17 Zones A-J: IF(weight > WB, ((weight-WB)*per_lb) + min, min)
        # When a row carries no explicit weight_break (legacy CSV uploads
        # and the admin form both allow it), derive WB from min/per_lb the
        # same way the workbook does (`G45 = F45/E45`). Without this
        # fallback an A-J quote on such a row would silently flatten to
        # `min` regardless of weight, under-quoting heavy shipments.
        effective_wb = weight_break
        if effective_wb is None and per_lb > 0:
            effective_wb = min_charge / per_lb
        if effective_wb is not None and weight > effective_wb:
            base = ((weight - effective_wb) * per_lb) + min_charge
        else:
            base = min_charge

    # Zone X and the NYC override do not get a fuel-surcharge multiplier
    # (the workbook's *1.315 only fires in D17, the A-J branch).
    if zone.upper() == "X":
        fuel_pct = 0.0
    else:
        fuel_pct = float(rate.fuel_pct or 0.0)
    base_surcharge_amount = base * fuel_pct
    base_with_fuel = base + base_surcharge_amount

    nyc_override_applied = False
    if str(destination).strip() in NYC_FLAT_RATE_ZIPS:
        # MAX(D17, D18) — the NYC flat rate wins only if it beats the
        # standard zone path. The override has no fuel surcharge.
        if NYC_FLAT_RATE_USD > base_with_fuel:
            base = NYC_FLAT_RATE_USD
            base_surcharge_amount = 0.0
            base_with_fuel = NYC_FLAT_RATE_USD
            fuel_pct = 0.0
            nyc_override_applied = True

    # VSC zone = MAX(origin VSC zone, destination VSC zone) to mirror the
    # workbook's K10 = MAX(K8, K9). Strip incoming ZIPs the same way the
    # NYC-override check does so accidental whitespace from a form
    # submission doesn't quietly collapse a real zone into "NATIONAL".
    origin_vsc_zone, origin_warnings = _resolve_destination_zone(
        str(origin).strip(),
        rate_set=rate_set,
        zip_lookup=zip_lookup,
        vsc_zone_lookup=vsc_zone_lookup,
    )
    dest_vsc_zone, dest_warnings = _resolve_destination_zone(
        str(destination).strip(),
        rate_set=rate_set,
        zip_lookup=zip_lookup,
        vsc_zone_lookup=vsc_zone_lookup,
    )

    def _zone_sort_key(z: str) -> tuple[int, str]:
        # Numeric zones (1-10) compare normally; "NATIONAL" / non-numeric
        # fall back to a low rank so a real zone beats the fallback.
        try:
            return (int(z), z)
        except (TypeError, ValueError):
            return (-1, str(z))

    vsc_zone = max(origin_vsc_zone, dest_vsc_zone, key=_zone_sort_key)
    warning_metadata = origin_warnings + dest_warnings

    dynamic_vsc_pct = get_dynamic_vsc_pct(
        base=base,
        miles=miles,
        zone=zone,
        dest_zone=vsc_zone,
        rate_set=rate_set,
    )
    vsc_amount = base_with_fuel * dynamic_vsc_pct
    total_fsc_applied = (base_surcharge_amount + vsc_amount) / base if base else 0.0
    quote_total = base_with_fuel + vsc_amount + accessorial_total

    return {
        "zone": zone,
        "miles": miles,
        "quote_total": quote_total,
        "base_rate": base,
        "fuel_surcharge_base_pct": fuel_pct,
        "fuel_surcharge_base_amount": base_surcharge_amount,
        "vsc_pct": dynamic_vsc_pct,
        "vsc_amount": vsc_amount,
        "total_fsc_applied": total_fsc_applied,
        "weight_break": weight_break,
        "per_lb": per_lb,
        "per_mile": per_mile,
        "min_charge": min_charge,
        "dest_zone": dest_vsc_zone,
        "origin_vsc_zone": origin_vsc_zone,
        "vsc_zone_used": vsc_zone,
        "nyc_override_applied": nyc_override_applied,
        "warning_metadata": warning_metadata,
    }
