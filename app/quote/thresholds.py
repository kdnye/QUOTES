"""Utility functions for quote limit warnings."""

THRESHOLD_WARNING = (
    "Warning! Quote exceeds the limits of this tool please call FSI directly for the most accurate quote. "
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
