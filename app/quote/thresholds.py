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


def check_air_piece_limit(quote_type: str, weight: float, pieces: int) -> str | None:
    """Return an error message if air freight exceeds 300 lbs per piece."""
    if quote_type.lower() == "air" and pieces > 0:
        if (weight / pieces) > 300:
            # Use parentheses to safely combine the long message across multiple lines
            return (
                "Warning! Air freight shipments with pieces greater than 300 lbs each "
                "exceeds the limits of this tool. Please contact FSI directly for the "
                "most accurate quote. Main Office: 800-651-0423 | "
                "Fax: 520-777-3853 | Email: Operations@freightservices.net"
            )
    return None
