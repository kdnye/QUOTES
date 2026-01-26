"""Utilities for managing named rate sets.

Rate data can be loaded in multiple variants (for example, customer-specific
pricing tiers). Each rate row is tagged with a ``rate_set`` identifier so quote
calculations can target the appropriate set for the requesting user.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from sqlalchemy.exc import OperationalError

from app.database import (
    Session,
    HotshotRate,
    AirCostZone,
    ZipZone,
    CostZone,
    BeyondRate,
)

DEFAULT_RATE_SET = "default"

# Known customer-specific rate sets that should always be available for admins to
# manage even before any rates are uploaded. The keys are normalized
# ``rate_set`` identifiers that appear in CSV uploads and database rows.
PRECONFIGURED_RATE_SETS: Dict[str, str] = {
    "agr": "Anatomy Gifts Registry",
    "inin": "Innoved Institute",
    "mdcr": "MedCure",
    "utn": "UTN",
    "meri": "MERI",
    "swiba": "SWIBA",
    "lsa": "Life Science Anotomical",
}


def normalize_rate_set(raw_value: Optional[str]) -> str:
    """Return a sanitized rate-set identifier.

    Args:
        raw_value: Optional user- or system-provided identifier.

    Returns:
        Lowercase rate set string. Empty or ``None`` inputs fall back to
        :data:`DEFAULT_RATE_SET`.
    """

    candidate = (raw_value or DEFAULT_RATE_SET).strip().lower()
    return candidate or DEFAULT_RATE_SET


def _call_with_rate_set(
    func: Callable[..., Any], rate_set: str, *args: Any, **kwargs: Any
) -> Any:
    """Invoke ``func`` with a ``rate_set`` keyword when supported.

    The helper preserves compatibility with callables that do not yet accept a
    ``rate_set`` parameter by retrying without the keyword when a ``TypeError``
    mentions ``rate_set``. Any other ``TypeError`` is re-raised so genuine
    argument issues still surface.

    Args:
        func: Callable to execute. Typically a lookup helper such as
            :func:`quote.logic_air.get_zip_zone`.
        rate_set: Identifier for the requested rate set. Passed as a keyword
            argument when supported.
        *args: Positional arguments forwarded to ``func``.
        **kwargs: Additional keyword arguments forwarded to ``func``.

    Returns:
        The return value of ``func`` either with or without the ``rate_set``
        keyword argument.
    """

    try:
        return func(*args, rate_set=rate_set, **kwargs)
    except TypeError as exc:  # pragma: no cover - compatibility path for legacy callers
        if "rate_set" not in str(exc):
            raise
        return func(*args, **kwargs)


def _collect_distinct_rate_sets(model) -> Iterable[str]:
    """Yield distinct ``rate_set`` values for ``model``.

    Wraps the query in a ``try`` so environments that have not yet run the
    migrations still succeed without raising ``OperationalError``.
    """

    try:
        with Session() as session:
            rows = session.query(model.rate_set).distinct().all()
            for (value,) in rows:
                if value:
                    yield str(value)
    except OperationalError:
        return []


def get_available_rate_sets() -> List[str]:
    """Return all known rate sets across the rate tables.

    Ensures :data:`DEFAULT_RATE_SET` is always present even when the tables are
    empty so forms and validation have a stable option. Preconfigured customer
    codes are also included to let administrators download blank templates and
    stage uploads before data exists in the database.
    """

    discovered: Set[str] = {DEFAULT_RATE_SET, *PRECONFIGURED_RATE_SETS.keys()}
    for model in (HotshotRate, AirCostZone, ZipZone, CostZone, BeyondRate):
        discovered.update(_collect_distinct_rate_sets(model))

    ordered_sets = [DEFAULT_RATE_SET, *sorted(discovered - {DEFAULT_RATE_SET})]
    return ordered_sets
