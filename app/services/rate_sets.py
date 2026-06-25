"""Utilities for managing named rate sets.

Rate data can be loaded in multiple variants (for example, customer-specific
pricing tiers). Each rate row is tagged with a ``rate_set`` identifier so quote
calculations can target the appropriate set for the requesting user.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import (
    Session,
    HotshotRate,
    AirCostZone,
    ZipZone,
    CostZone,
    BeyondRate,
)
from app.models import RATE_SET_DEFAULT, RATE_SET_SCIENCE_CARE

# Sourced from app.models.RATE_SET_DEFAULT so the two historically
# divergent names stay in lock-step. Both spellings exist for back-
# compat (RATE_SET_DEFAULT in app/models.py is imported by migrations
# and column definitions; DEFAULT_RATE_SET here is imported by quote
# logic and routes). Changing the literal in app/models.py now updates
# both call paths atomically.
DEFAULT_RATE_SET = RATE_SET_DEFAULT

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

# Default endpoint used after login when no rate-set-specific landing
# is configured.
DEFAULT_LANDING_ENDPOINT = "quotes.new_quote"

# Maps normalized rate-set identifiers to the Flask endpoint a user
# should be redirected to after authenticating. Add additional rate
# sets here to give them a custom landing page; any rate set not
# present falls back to :data:`DEFAULT_LANDING_ENDPOINT`. Both the
# legacy ``"scicr"`` tag used in admin assignments and the
# ``"science_care"`` constant are mapped to the multi-lab quote form.
RATE_SET_LANDING_ENDPOINTS: Dict[str, str] = {
    "scicr": "science_care.sc_quote_form",
    RATE_SET_SCIENCE_CARE: "science_care.sc_quote_form",
}

# Rate sets that should be treated as Science Care for tenant
# authorization. Kept here so :mod:`app.policies` and the landing
# map stay aligned.
SCIENCE_CARE_RATE_SETS: frozenset[str] = frozenset(
    {"scicr", RATE_SET_SCIENCE_CARE}
)


def landing_endpoint_for_user(user: Any) -> str:
    """Return the Flask endpoint a user should land on after login.

    Args:
        user: Authenticated principal whose ``rate_set`` attribute is read.
            ``None`` and objects without a ``rate_set`` attribute fall back
            to :data:`DEFAULT_LANDING_ENDPOINT`.

    Returns:
        Endpoint name suitable for :func:`flask.url_for`. Looks up the
        user's normalized ``rate_set`` in :data:`RATE_SET_LANDING_ENDPOINTS`
        and falls back to :data:`DEFAULT_LANDING_ENDPOINT` when no mapping
        is configured.
    """

    raw_rate_set = getattr(user, "rate_set", None)
    normalized = normalize_rate_set(raw_rate_set)
    return RATE_SET_LANDING_ENDPOINTS.get(normalized, DEFAULT_LANDING_ENDPOINT)


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


def query_with_rate_set_fallback(
    session: Any,
    model: Any,
    rate_set: str,
    **filters: Any,
) -> Any:
    """Look up a row by ``filters`` + ``rate_set`` with a DEFAULT fallback.

    Tries
    ``session.query(model).filter_by(rate_set=rate_set, **filters).first()``.
    When that returns ``None`` AND ``rate_set != DEFAULT_RATE_SET``,
    retries against :data:`DEFAULT_RATE_SET`. Returns the first row
    found, or ``None`` if neither query matches.

    Args:
        session: An open SQLAlchemy session. The helper does not open
            or close sessions; callers continue to wrap their work in
            ``with Session() as db:``.
        model: The mapped class to query (e.g. ``ZipZone``,
            ``CostZone``).
        rate_set: Pre-normalised rate-set identifier. Callers should
            run their input through :func:`normalize_rate_set` first.
        **filters: Additional equality filters forwarded to
            ``filter_by``. Use only when the column has a direct
            equality match - range predicates and ``order_by`` need a
            different helper.

    Returns:
        The first matching row, or ``None``.
    """

    record = (
        session.query(model)
        .filter_by(rate_set=rate_set, **filters)
        .first()
    )
    if record is None and rate_set != DEFAULT_RATE_SET:
        record = (
            session.query(model)
            .filter_by(rate_set=DEFAULT_RATE_SET, **filters)
            .first()
        )
    return record


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

    Wraps the query in a ``try`` so environments that have not yet run
    the migrations still succeed. Catches both ``OperationalError``
    (SQLite raises this for a missing table) and ``ProgrammingError``
    (psycopg2 raises ``UndefinedTable`` -- a ``ProgrammingError`` --
    on Postgres for the same condition). Without the Postgres-side
    catch, any caller of :func:`get_available_rate_sets` that runs in
    a context where the rate tables haven't been created (unit tests,
    fresh DB before ``flask db upgrade``) crashes on Postgres only.
    """

    try:
        with Session() as session:
            rows = session.query(model.rate_set).distinct().all()
            for (value,) in rows:
                if value:
                    yield str(value)
    except (OperationalError, ProgrammingError):
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
