"""Utility functions for quote limit warnings."""

THRESHOLD_WARNING = (
    "Warning! Quote exceeds the limits of this tool please call FSI directly for the most accurate quote. "
    "Main Office: 800-651-0423 | Fax: 520-777-3853 | Email: Operations@freightservices.net"
)

AIR_PIECE_LIMIT_WARNING = (
    "Warning! Air freight shipments with pieces greater than 300 lbs each exceeds the limits of this tool. "
    "Please contact FSI directly for the most accurate quote. "
    "Main Office: 800-651-0423 | Fax: 520-777-3853 | Email: Operations@freightservices.net"
)


def check_thresholds(quote_type: str, weight: float, total: float) -> str:
    """Return a warning message when quote values exceed allowed limits.

    Parameters
    ----------
    quote_type:
        Type of quote (``"Hotshot"`` or ``"Air"``).
    weight:
        Billable shipment weight in pounds.
    total:
        Total quoted price in USD.

    Returns
    -------
    str
        :data:`THRESHOLD_WARNING` if any limit is exceeded, otherwise ``""``.
    """

    if quote_type.lower() == "air" and weight > 1200:
        return THRESHOLD_WARNING
    if weight > 3000 or total > 6000:
        return THRESHOLD_WARNING
    return ""


def check_air_piece_limit(
    quote_type: str,
    actual_weight: float,
    pieces: int,
    dim_weight: float = 0.0,
) -> str | None:
    """Return an error message when air freight exceeds 300 billable lbs/piece.

    Inputs:
        quote_type: Shipment mode (for example, ``"Air"``).
        actual_weight: Scale weight in pounds for the full shipment.
        pieces: Number of pieces in the shipment.
        dim_weight: Optional pre-calculated dimensional weight in pounds for
            the full shipment.

    Output:
        The :data:`AIR_PIECE_LIMIT_WARNING` message when the shipment is air
        freight and the billable pounds-per-piece is over 300, otherwise
        ``None``.

    External dependencies:
        None.
    """
    if quote_type.lower() == "air" and pieces > 0:
        billable_weight = max(actual_weight, dim_weight)
        if (billable_weight / pieces) > 300:
            return AIR_PIECE_LIMIT_WARNING
    return None
