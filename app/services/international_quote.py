"""Stub for FSI International quote math.

Mirrors the ``International Quotes`` tab of the FSI Shipping Quote Tool
2026 VSC-Locked workbook. This is a **read-only** stub — it computes the
quote per the workbook's ``R21`` formula and returns the breakdown but
does NOT persist anything, has no VSC, no accessorials, and no UI yet.
The intended caller today is the SC multi-leg orchestrator (or a future
``/sc/international/quote`` endpoint) once we wire it up.

Quote (from workbook ``R21``):

    IF(weight > weight_break,
       ((weight - weight_break) * per_lb) + min_charge,
       min_charge)
    + intl_hotshot_surcharge

Where ``intl_hotshot_surcharge`` is non-zero only when:
    * the lane is Door-to-Door (``SCInternationalLane.notes == "Door to Door"``)
    * the lane is a Standard rate (not Customer Specific)
    * ``km_to_airport > 80``
and equals ``(round(km_to_airport) - 80) * cost_per_km_over_80``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.database import Session
from app.models import SCInternationalLane

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


def calculate_international_quote(
    *,
    destination: str,
    lab_code: str,
    weight_lb: float,
    km_to_airport: Optional[float] = None,
    rate_set: str = "science_care",
    lane_lookup=lookup_lane,
) -> InternationalQuote:
    """Run the workbook's ``R21`` math against a ``SCInternationalLane`` row.

    ``km_to_airport`` is the Google Distance Matrix value from the
    destination city to the lane's airport (workbook ``AA8``). When the
    caller doesn't supply it (most do not yet), the int'l hotshot
    surcharge is left at $0 and a warning is emitted — the door-to-door
    leg of the calc requires that distance.
    """
    lane = lane_lookup(destination=destination, lab_code=lab_code, rate_set=rate_set)
    result = InternationalQuote(
        destination=destination,
        lab_code=lab_code.strip().upper(),
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
            result.warnings.append(
                "Door-to-Door lane requires distance from destination city to "
                "airport (in km) to compute the international-hotshot surcharge; "
                "supply km_to_airport to refine the quote."
            )
        elif km_to_airport > 80:
            intl_hotshot = (round(km_to_airport) - 80) * float(
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
