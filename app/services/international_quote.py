"""FSI International quote math.

Mirrors the ``International Quotes`` tab of the FSI Shipping Quote Tool
2026 VSC-Locked workbook. The function is callable from a route /
orchestrator today; persistence + a dedicated form are still pending.

Quote (from workbook ``R21``):

    IF(weight > weight_break,
       ((weight - weight_break) * per_lb) + min_charge,
       min_charge)
    + intl_hotshot_surcharge

Where ``intl_hotshot_surcharge`` is non-zero only when:
    * the lane is Door-to-Door (``SCInternationalLane.notes == "Door to Door"``)
    * the lane is a Standard rate (not Customer Specific)
    * ``km_to_airport > 80``
and equals ``(int(km_to_airport + 0.5) - 80) * cost_per_km_over_80``.

``km_to_airport`` is resolved automatically against Google Directions
(mirroring the workbook's ``G_DISTANCE`` VBA + ``AA8 = MIN(W9:W18)``
pattern) when not supplied by the caller. The lane carries up to three
candidate airports; the runtime asks Google for the driving distance from
each ``"{IATA} Airport"`` to ``"City of {city}, {country}"`` and picks
the smallest. Tests inject a stubbed ``distance_lookup`` so they don't
hit the live API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.database import Session
from app.models import SCInternationalLane
from app.quote.distance import get_km_to_nearest_airport

# Workbook constant — quotes above this need ops confirmation. Surfaced in
# the return dict so the caller can route the quote into a human review
# queue instead of straight to the customer.
INTL_HOTSHOT_CONFIRM_THRESHOLD_USD = 750.0
DOOR_TO_DOOR = "Door to Door"
DOOR_TO_AIRPORT = "Door to Airport"
RATE_CLASS_STANDARD = "Standard"


@dataclass
class InternationalQuote:
    """Outcome of one International quote.

    ``warnings`` carries human-readable strings the UI / email body should
    show ("Quote requires FSI confirmation", "Special Notes from the rate
    row", etc.). ``error`` is non-None only when the lane is missing from
    :class:`SCInternationalLane`.

    ``picked_airport`` is the IATA code Google picked as closest to the
    destination city, when the runtime resolved ``km_to_airport``
    automatically. ``None`` when the caller supplied ``km_to_airport``
    or the lookup couldn't resolve a winner.
    """

    destination: str
    lab_code: str
    weight_lb: float
    base: float = 0.0
    intl_hotshot_surcharge: float = 0.0
    quote_total: float = 0.0
    requires_confirmation: bool = False
    lane: Optional[SCInternationalLane] = None
    km_to_airport: Optional[float] = None
    picked_airport: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


def lookup_lane(
    *,
    destination: str,
    lab_code: str,
    rate_set: str = "science_care",
) -> Optional[SCInternationalLane]:
    """Resolve one :class:`SCInternationalLane` by (destination, lab)."""
    with Session() as db:
        return (
            db.query(SCInternationalLane)
            .filter_by(
                destination=destination.strip(),
                lab_code=lab_code.strip().upper(),
                rate_set=rate_set,
            )
            .first()
        )


def _parse_city_from_destination(destination: str) -> str:
    """Pull the city out of a display string like ``"Australia - Adelaide"``.

    The workbook destination dropdown uses ``"{Country} - {City}"``. If
    the caller doesn't supply ``destination_city`` explicitly, treat the
    text after the last ``" - "`` as the city. Strings without a `` - ``
    return as-is — that lets a caller pass a bare city name and still
    get a lookup.
    """

    if " - " in destination:
        return destination.rsplit(" - ", 1)[-1].strip()
    return destination.strip()


def _candidate_airports(lane: SCInternationalLane) -> list[str]:
    return [
        code
        for code in (lane.airport_code_1, lane.airport_code_2, lane.airport_code_3)
        if code and str(code).strip()
    ]


def calculate_international_quote(
    *,
    destination: str,
    lab_code: str,
    weight_lb: float,
    km_to_airport: Optional[float] = None,
    destination_city: Optional[str] = None,
    destination_country: Optional[str] = None,
    rate_set: str = "science_care",
    lane_lookup=lookup_lane,
    distance_lookup=None,
) -> InternationalQuote:
    """Run the workbook's ``R21`` math against a ``SCInternationalLane`` row.

    Parameters
    ----------
    destination:
        Lane display string (``"{Country} - {City}"`` per the workbook).
    lab_code:
        SC origin lab (``SCAZ`` / ``SCCA`` / …). Normalized at entry.
    weight_lb:
        Billable weight in pounds.
    km_to_airport:
        Optional override. When ``None`` (the default) the runtime
        resolves it via Google Directions against the lane's 1-3 airport
        codes — mirrors the workbook's ``AA8 = MIN(W9:W18)``. Pass a
        value here to override or to skip the network call.
    destination_city / destination_country:
        Inputs to the Google lookup. When omitted the runtime parses
        ``destination`` (city) and reads ``lane.country`` (country).
    rate_set:
        Lane partition. Defaults to ``science_care``.
    lane_lookup / distance_lookup:
        Injectable for tests. ``distance_lookup`` is the low-level
        ``(origin, destination)`` -> km callable; defaults to the
        Google Directions wrapper in :mod:`app.quote.distance`.
    """
    # Normalize at the entry point so the error string, the result payload,
    # and the lane lookup all see the same canonical key.
    destination = str(destination or "").strip()
    lab_code = str(lab_code or "").strip().upper()
    lane = lane_lookup(destination=destination, lab_code=lab_code, rate_set=rate_set)
    result = InternationalQuote(
        destination=destination,
        lab_code=lab_code,
        weight_lb=float(weight_lb),
        km_to_airport=km_to_airport,
        lane=lane,
    )

    if lane is None:
        result.error = (
            f"No International lane configured for "
            f"destination={destination!r} lab={lab_code!r}"
        )
        return result

    # Auto-resolve km_to_airport when the caller didn't pass it explicitly.
    # Only worth doing for Door-to-Door Standard lanes — the others ignore
    # the surcharge anyway, and the Google call costs $0.005 per request.
    auto_resolve_eligible = (
        km_to_airport is None
        and (lane.notes or "").strip() == DOOR_TO_DOOR
        and (lane.rate_class or "").strip() == RATE_CLASS_STANDARD
        and lane.cost_per_km_over_80 is not None
    )
    if auto_resolve_eligible:
        resolved_city = (destination_city or _parse_city_from_destination(destination))
        resolved_country = destination_country or lane.country
        airports = _candidate_airports(lane)
        if not airports:
            result.warnings.append(
                "Door-to-Door lane has no airport codes configured; cannot "
                "auto-resolve distance to airport — supply km_to_airport "
                "manually."
            )
        elif not resolved_city:
            result.warnings.append(
                "Door-to-Door lane requires a destination city to auto-resolve "
                "the distance to airport; supply destination_city or "
                "km_to_airport."
            )
        else:
            km, picked = get_km_to_nearest_airport(
                destination_city=resolved_city,
                destination_country=resolved_country,
                airport_codes=airports,
                distance_lookup=distance_lookup,
            )
            if km is None:
                result.warnings.append(
                    "Google Distance Matrix lookup failed for "
                    f"city={resolved_city!r} airports={airports}; "
                    "supply km_to_airport manually to override."
                )
            else:
                km_to_airport = km
                result.km_to_airport = km
                result.picked_airport = picked

    weight_break = float(lane.weight_break or 0.0)
    per_lb = float(lane.per_lb or 0.0)
    min_charge = float(lane.min_charge or 0.0)

    if weight_lb > weight_break and weight_break > 0:
        base = ((weight_lb - weight_break) * per_lb) + min_charge
    else:
        base = min_charge

    intl_hotshot = 0.0
    if (
        (lane.notes or "").strip() == DOOR_TO_DOOR
        and (lane.rate_class or "").strip() == RATE_CLASS_STANDARD
        and lane.cost_per_km_over_80 is not None
    ):
        if km_to_airport is None:
            # Either the caller didn't supply it AND we couldn't auto-resolve
            # (warning already on the result), or it was explicitly omitted.
            # Either way, no surcharge.
            pass
        elif km_to_airport > 80:
            # Excel's ROUND(x, 0) rounds half away from zero; Python's
            # round() uses banker's rounding (round half to even). Use the
            # ``int(x + 0.5)`` idiom so the workbook's AA10 produces the
            # same surcharge dollar for halfway cases (e.g. 80.5 -> 81).
            intl_hotshot = (int(km_to_airport + 0.5) - 80) * float(
                lane.cost_per_km_over_80
            )

    result.base = base
    result.intl_hotshot_surcharge = intl_hotshot
    result.quote_total = base + intl_hotshot

    if intl_hotshot > INTL_HOTSHOT_CONFIRM_THRESHOLD_USD:
        result.requires_confirmation = True
        result.warnings.append(
            "International hotshot surcharge exceeds "
            f"${INTL_HOTSHOT_CONFIRM_THRESHOLD_USD:,.0f}; confirm the quote "
            "with FSI before sending to the customer."
        )
    if lane.special_notes:
        result.warnings.append(lane.special_notes)
    return result


__all__ = [
    "DOOR_TO_AIRPORT",
    "DOOR_TO_DOOR",
    "INTL_HOTSHOT_CONFIRM_THRESHOLD_USD",
    "InternationalQuote",
    "RATE_CLASS_STANDARD",
    "calculate_international_quote",
    "lookup_lane",
]
