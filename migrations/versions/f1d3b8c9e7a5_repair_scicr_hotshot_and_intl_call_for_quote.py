"""repair scicr hotshot insert + drop "Call for Quote" intl lanes

Revision ID: f1d3b8c9e7a5
Revises: e9b3a7c4d28f
Create Date: 2026-06-25 19:00:00.000000

Two follow-up fixes for bugs landed in PR #328 that Codex flagged after
merge:

1. ``e9b3a7c4d28f`` upserts ``hotshot_rates`` rows for ``rate_set =
   'scicr'`` but the ``INSERT ... WHERE NOT EXISTS`` branch omits the
   ``miles`` column (non-nullable on :class:`HotshotRate`) and would
   have inserted ONE row per zone-letter instead of one row per mile
   (rows 1-9 for Zone A, 10-19 for B, ..., 90-99 for J, 100 for X).

   The first INSERT here backfills missing scicr rows by **copying
   from default** for every ``(miles, zone)`` pair default carries.
   That preserves the per-mile granularity the schema expects and
   guarantees ``miles`` is populated correctly.

   The follow-up UPDATE re-applies the FSI VSC-Locked values to every
   scicr row (including any new ones we just inserted from default's
   stale values) so the rate-set ends up aligned with ``default``.
   Same numbers as ``e9b3a7c4d28f``.

2. ``d8a4f9c1b2e6`` seeded 84 "Call for Quote" placeholder rows from
   ``rates/international_lanes.csv`` as zero-priced lanes
   (``_to_float("Call for Quote", allow_blank=False) -> 0.0``). The
   runtime now matches those rows and returns a $0 quote instead of
   routing the customer to "Contact FSI for Quote". Cleaning the CSV
   in the same commit means future re-runs won't re-seed them; this
   DELETE catches any DB that already imported the buggy rows.

The downgrade is intentionally a no-op for both fixes — restoring the
broken state would only re-create the bugs.
"""

from typing import Dict, Sequence, Tuple, Union

from alembic import op
from sqlalchemy import text


revision: str = "f1d3b8c9e7a5"
down_revision: Union[str, Sequence[str], None] = "e9b3a7c4d28f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same FSI VSC-Locked values as e9b3a7c4d28f / c5d7f1e9a2b3. Keyed by
# zone letter; applied to every matching scicr row regardless of mile
# bucket. Zone X is handled separately because it carries per_mile.
_HOTSHOT_RATES_A_TO_J: Dict[str, Tuple[float, float, float, float]] = {
    "A": (0.2464,  70.0128,  284.142857, 0.315),
    "B": (0.2464,  81.4528,  330.571429, 0.315),
    "C": (0.2464,  93.3504,  378.857143, 0.315),
    "D": (0.2464, 105.2480,  427.142857, 0.315),
    "E": (0.2464, 117.1456,  475.428571, 0.315),
    "F": (0.2464, 219.6480,  891.428571, 0.315),
    "G": (0.2464, 267.2384, 1084.571429, 0.315),
    "H": (0.2464, 267.2384, 1084.571429, 0.315),
    "I": (0.2464, 297.4400, 1207.142857, 0.315),
    "J": (0.2464, 361.5040, 1467.142857, 0.315),
}
_HOTSHOT_ZONE_X = {
    "per_lb": 5.1,
    "per_mile": 6.0192,
    "min_charge": 5.2,
    "weight_break": 22_000_000.0,
    "fuel_pct": 0.0,
}
SCICR = "scicr"


def upgrade() -> None:
    # --- Fix 1a: backfill missing scicr hotshot rows from default. -----
    # Copy every default row whose (miles, zone) has no scicr counterpart.
    op.execute(
        text(
            "INSERT INTO hotshot_rates "
            "(rate_set, miles, zone, per_lb, per_mile, "
            " min_charge, weight_break, fuel_pct) "
            "SELECT :scicr, d.miles, d.zone, d.per_lb, d.per_mile, "
            "       d.min_charge, d.weight_break, d.fuel_pct "
            "FROM hotshot_rates d "
            "WHERE d.rate_set = 'default' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM hotshot_rates s "
            "    WHERE s.rate_set = :scicr "
            "      AND s.miles = d.miles "
            "      AND UPPER(s.zone) = UPPER(d.zone))"
        ).bindparams(scicr=SCICR)
    )

    # --- Fix 1b: re-apply FSI values to every scicr row (idempotent). --
    update_a_j = text(
        "UPDATE hotshot_rates "
        "SET per_lb = :per_lb, "
        "    min_charge = :min_charge, "
        "    weight_break = :weight_break, "
        "    fuel_pct = :fuel_pct, "
        "    per_mile = NULL "
        "WHERE rate_set = :rate_set AND UPPER(zone) = :zone"
    )
    for zone, (per_lb, min_charge, weight_break, fuel_pct) in _HOTSHOT_RATES_A_TO_J.items():
        op.execute(
            update_a_j.bindparams(
                per_lb=per_lb,
                min_charge=min_charge,
                weight_break=weight_break,
                fuel_pct=fuel_pct,
                rate_set=SCICR,
                zone=zone,
            )
        )
    op.execute(
        text(
            "UPDATE hotshot_rates "
            "SET per_lb = :per_lb, "
            "    per_mile = :per_mile, "
            "    min_charge = :min_charge, "
            "    weight_break = :weight_break, "
            "    fuel_pct = :fuel_pct "
            "WHERE rate_set = :rate_set AND UPPER(zone) = 'X'"
        ).bindparams(
            per_lb=_HOTSHOT_ZONE_X["per_lb"],
            per_mile=_HOTSHOT_ZONE_X["per_mile"],
            min_charge=_HOTSHOT_ZONE_X["min_charge"],
            weight_break=_HOTSHOT_ZONE_X["weight_break"],
            fuel_pct=_HOTSHOT_ZONE_X["fuel_pct"],
            rate_set=SCICR,
        )
    )

    # --- Fix 2: drop the "Call for Quote" international lanes. ---------
    # The seed CSV in this commit no longer includes those rows, but any
    # DB that ran d8a4f9c1b2e6 before this fix has 84 of them sitting
    # at min=0 / per_lb=0. Delete them so the runtime falls through to
    # the "Contact FSI for Quote" error path instead of silently quoting
    # $0.
    op.execute(
        text(
            "DELETE FROM sc_international_lanes "
            "WHERE min_charge <= 0 OR per_lb <= 0"
        )
    )


def downgrade() -> None:
    # Restoring the broken state would re-introduce the bugs; intentional no-op.
    pass
