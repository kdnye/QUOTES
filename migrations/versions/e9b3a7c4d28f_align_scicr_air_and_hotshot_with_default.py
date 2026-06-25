"""mirror FSI VSC-Locked rates into the scicr rate set (Air + Hotshot)

Revision ID: e9b3a7c4d28f
Revises: d8a4f9c1b2e6
Create Date: 2026-06-25 18:00:00.000000

The two prior alignment migrations only touched ``rate_set = 'default'``
(``f3a8c2b9d1e4`` for ``air_cost_zones`` and ``c5d7f1e9a2b3`` for
``hotshot_rates``). The Science Care rate set (``scicr`` / preconfigured
SC tier) needs the same numbers — otherwise SC quotes will continue to
diverge from the FSI Shipping Quote Tool 2026 VSC-Locked workbook for
any rows where ``scicr`` already has an explicit override.

Hotshot UPDATE-only (PATCH 2026-06-25): the original revision of this
file also tried to ``INSERT ... WHERE NOT EXISTS`` for missing scicr
hotshot rows, but that INSERT omitted the non-nullable ``miles`` column
and would have inserted one row per zone letter instead of one per mile
(the schema is one row per mile bucket 1-100). On a fresh DB where
``scicr`` was empty, Alembic's per-migration transaction would have
rolled the whole thing back — meaning the broken predecessor blocked
the downstream repair from ever running. The hotshot INSERTs are
stripped here so the migration is purely UPDATE — a no-op on rows that
don't exist, the right answer on rows that do. The follow-up
``f1d3b8c9e7a5`` then copies missing scicr rows from ``default`` (with
``miles`` populated) and re-applies the FSI values.

Air-side UPSERT is unchanged — ``air_cost_zones`` has no ``miles``
column, so the insert path there was always correct.

Same exact numbers as ``f3a8c2b9d1e4`` (Air) and ``c5d7f1e9a2b3``
(Hotshot) — keep these lists in sync if either source changes.
"""

from typing import Dict, Sequence, Tuple, Union

from alembic import op
from sqlalchemy import text


revision: str = "e9b3a7c4d28f"
down_revision: Union[str, Sequence[str], None] = "d8a4f9c1b2e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (min_charge, per_lb, weight_break) — same source as f3a8c2b9d1e4.
_AIR_RATES: Dict[str, Tuple[float, float, float]] = {
    "A": (235.75552, 1.4551680, 162.012579),
    "B": (222.997632, 1.5832960, 140.843931),
    "C": (248.513408, 1.7571840, 141.427083),
    "D": (286.777920, 1.9402240, 147.806604),
    "E": (331.430528, 2.0592000, 160.951111),
    "F": (407.968704, 2.1873280, 186.514644),
    "G": (427.105536, 2.2422400, 190.481633),
    "H": (465.379200, 2.4893440, 186.948529),
}

# (per_lb, min_charge, weight_break, fuel_pct) — same source as c5d7f1e9a2b3.
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

# Zone X for hotshot: per_mile is the real driver, fuel_pct must be 0.
_HOTSHOT_ZONE_X = {
    "per_lb": 5.1,
    "per_mile": 6.0192,
    "min_charge": 5.2,
    "weight_break": 22_000_000.0,
    "fuel_pct": 0.0,
}

SCICR = "scicr"


def _upsert_air(zone: str, min_charge: float, per_lb: float, weight_break: float) -> None:
    op.execute(
        text(
            "UPDATE air_cost_zones "
            "SET min_charge = :min_charge, "
            "    per_lb = :per_lb, "
            "    weight_break = :weight_break "
            "WHERE rate_set = :rate_set AND zone = :zone"
        ).bindparams(
            min_charge=min_charge,
            per_lb=per_lb,
            weight_break=weight_break,
            rate_set=SCICR,
            zone=zone,
        )
    )
    op.execute(
        text(
            "INSERT INTO air_cost_zones (rate_set, zone, min_charge, per_lb, weight_break) "
            "SELECT :rate_set, :zone, :min_charge, :per_lb, :weight_break "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM air_cost_zones "
            "  WHERE rate_set = :rate_set AND zone = :zone)"
        ).bindparams(
            rate_set=SCICR,
            zone=zone,
            min_charge=min_charge,
            per_lb=per_lb,
            weight_break=weight_break,
        )
    )


def _update_hotshot_a_to_j(
    zone: str, per_lb: float, min_charge: float, weight_break: float, fuel_pct: float
) -> None:
    # UPDATE-only — no INSERT here. See module docstring for why.
    # Missing rows are backfilled from default's per-mile rows by the
    # follow-up ``f1d3b8c9e7a5`` migration.
    op.execute(
        text(
            "UPDATE hotshot_rates "
            "SET per_lb = :per_lb, "
            "    min_charge = :min_charge, "
            "    weight_break = :weight_break, "
            "    fuel_pct = :fuel_pct, "
            "    per_mile = NULL "
            "WHERE rate_set = :rate_set AND UPPER(zone) = :zone"
        ).bindparams(
            per_lb=per_lb,
            min_charge=min_charge,
            weight_break=weight_break,
            fuel_pct=fuel_pct,
            rate_set=SCICR,
            zone=zone,
        )
    )


def _update_hotshot_zone_x() -> None:
    # UPDATE-only — same reasoning as ``_update_hotshot_a_to_j``.
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
    # Note: an INSERT path that copied missing scicr rows used to live
    # here, but it omitted the non-nullable ``miles`` column and would
    # have inserted one row per zone-letter instead of one per mile
    # (1-100). It now lives correctly in ``f1d3b8c9e7a5`` as a
    # ``SELECT FROM hotshot_rates WHERE rate_set='default'`` copy.


def upgrade() -> None:
    for zone, (min_charge, per_lb, weight_break) in _AIR_RATES.items():
        _upsert_air(zone, min_charge, per_lb, weight_break)
    for zone, (per_lb, min_charge, weight_break, fuel_pct) in _HOTSHOT_RATES_A_TO_J.items():
        _update_hotshot_a_to_j(zone, per_lb, min_charge, weight_break, fuel_pct)
    _update_hotshot_zone_x()


def downgrade() -> None:
    # The prior rates that lived in scicr before this migration are not
    # known here — they could have been hand-edited per customer. Rather
    # than guess at restore values, the downgrade is a no-op. Re-run the
    # forward migration if you want the FSI-aligned values back.
    pass
