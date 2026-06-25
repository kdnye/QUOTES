"""align AirCostZone rows with FSI VSC-Locked 2026 workbook

Revision ID: f3a8c2b9d1e4
Revises: e1f2a3b4c5d6
Create Date: 2026-06-25 16:00:00.000000

The 8 air cost zone rates that drive ``calculate_air_quote()`` were sourced
from an older "VSC baked in" master and read ~13% high vs the FSI Shipping
Quote Tool 2026 VSC-Locked workbook (the authoritative card the company
issues against). Every SCxx -> SLC test Michael ran came back $140-190 above
the FSI tool's number, all attributable to this rate-card drift.

This migration replaces the DEFAULT rate set's :class:`app.models.AirCostZone`
rows with the exact FSI workbook values (``Domestic Air Quotes!C4:E11``):

  Zone   Min          Per Lb        Weight Break
  ----   ----------   -----------   ------------
  A      235.75552    1.4551680     162.012579
  B      222.997632   1.5832960     140.843931
  C      248.513408   1.7571840     141.427083
  D      286.777920   1.9402240     147.806604
  E      331.430528   2.0592000     160.951111
  F      407.968704   2.1873280     186.514644
  G      427.105536   2.2422400     190.481633
  H      465.379200   2.4893440     186.948529

Per-customer rate sets (anything other than ``'default'``) are NOT touched -
those rows represent intentional overrides and should be reviewed manually.

The downgrade restores the prior ``rates/air_cost_zone.csv`` values.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f3a8c2b9d1e4"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FSI_RATES = {
    "A": (235.75552, 1.4551680, 162.012579),
    "B": (222.997632, 1.5832960, 140.843931),
    "C": (248.513408, 1.7571840, 141.427083),
    "D": (286.777920, 1.9402240, 147.806604),
    "E": (331.430528, 2.0592000, 160.951111),
    "F": (407.968704, 2.1873280, 186.514644),
    "G": (427.105536, 2.2422400, 190.481633),
    "H": (465.379200, 2.4893440, 186.948529),
}

_PRE_FSI_RATES = {
    "A": (267.90, 1.65, 162.0),
    "B": (253.41, 1.80, 141.0),
    "C": (282.40, 2.00, 141.0),
    "D": (325.88, 2.20, 148.0),
    "E": (376.63, 2.34, 161.0),
    "F": (463.60, 2.49, 187.0),
    "G": (485.35, 2.55, 190.0),
    "H": (528.84, 2.83, 187.0),
}


def _apply(rates: dict[str, tuple[float, float, float]]) -> None:
    for zone, (min_charge, per_lb, weight_break) in rates.items():
        op.execute(
            "UPDATE air_cost_zones "
            f"SET min_charge = {min_charge}, "
            f"    per_lb = {per_lb}, "
            f"    weight_break = {weight_break} "
            f"WHERE zone = '{zone}' AND rate_set = 'default'"
        )


def upgrade() -> None:
    _apply(_FSI_RATES)


def downgrade() -> None:
    _apply(_PRE_FSI_RATES)
