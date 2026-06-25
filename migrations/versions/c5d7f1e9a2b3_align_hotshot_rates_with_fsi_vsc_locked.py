"""align HotshotRate rows with FSI VSC-Locked 2026 workbook

Revision ID: c5d7f1e9a2b3
Revises: f3a8c2b9d1e4
Create Date: 2026-06-25 17:00:00.000000

The 11 Hotshot zone rows that drive ``calculate_hotshot_quote()`` were
sourced from an older rate card and drifted vs the FSI Shipping Quote Tool
2026 VSC-Locked workbook (``Domestic Hotshot Quotes!C44:N55``):

  Zone   Min        Per Lb    Weight Break   Fuel %
  ----   --------   -------   ------------   ------
  A       70.0128   0.2464    284.142857     0.315
  B       81.4528   0.2464    330.571429     0.315
  C       93.3504   0.2464    378.857143     0.315
  D      105.2480   0.2464    427.142857     0.315
  E      117.1456   0.2464    475.428571     0.315
  F      219.6480   0.2464    891.428571     0.315
  G      267.2384   0.2464   1084.571429     0.315
  H      267.2384   0.2464   1084.571429     0.315
  I      297.4400   0.2464   1207.142857     0.315
  J      361.5040   0.2464   1467.142857     0.315
  X        5.20     5.10      —              0.000   (per_mile 6.0192)

Per-Lb was 0.208 across the board; FSI uses 0.2464. Min charges were $10-50
above FSI. Zone X stored ``fuel_pct = 0.315`` even though the FSI
``Domestic Hotshot Quotes!D18`` (Zone X / NYC branch) has no ``*1.315``
multiplier — set to ``0`` so the runtime no longer over-charges Zone X.

The companion code change in ``app/quote/logic_hotshot.py`` switches the
A-J base formula from ``max(min, weight * per_lb)`` to
``IF(weight > weight_break, ((weight - weight_break) * per_lb) + min, min)``,
drops the Zone X per-lb floor, and uses ``MAX(origin_vsc_zone,
dest_vsc_zone)`` for the dynamic VSC lookup.

Only the ``default`` rate set is touched; per-customer rate sets remain as
authored.
"""

from typing import Dict, Sequence, Tuple, Union

from alembic import op
from sqlalchemy import text


revision: str = "c5d7f1e9a2b3"
down_revision: Union[str, Sequence[str], None] = "f3a8c2b9d1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (per_lb, min_charge, weight_break, fuel_pct)
_FSI_RATES: Dict[str, Tuple[float, float, float, float]] = {
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

_PRE_FSI_RATES: Dict[str, Tuple[float, float, float, float]] = {
    "A": (0.208,  79.56,  382.5, 0.315),
    "B": (0.208,  92.56,  445.0, 0.315),
    "C": (0.208, 119.60,  575.0, 0.315),
    "D": (0.208, 119.60,  575.0, 0.315),
    "E": (0.208, 133.12,  640.0, 0.315),
    "F": (0.208, 249.60, 1200.0, 0.315),
    "G": (0.208, 303.68, 1460.0, 0.315),
    "H": (0.208, 303.68, 1460.0, 0.315),
    "I": (0.208, 338.00, 1625.0, 0.315),
    "J": (0.208, 410.80, 1975.0, 0.315),
}

# Zone X stays per_mile=6.0192 in both directions; only fuel_pct flips.
_ZONE_X_FUEL_FSI: float = 0.0
_ZONE_X_FUEL_PRE: float = 0.315


def _apply_zones_a_to_j(rates: Dict[str, Tuple[float, float, float, float]]) -> None:
    stmt = text(
        "UPDATE hotshot_rates "
        "SET per_lb = :per_lb, "
        "    min_charge = :min_charge, "
        "    weight_break = :weight_break, "
        "    fuel_pct = :fuel_pct "
        "WHERE UPPER(zone) = :zone AND rate_set = 'default'"
    )
    for zone, (per_lb, min_charge, weight_break, fuel_pct) in rates.items():
        op.execute(
            stmt.bindparams(
                per_lb=per_lb,
                min_charge=min_charge,
                weight_break=weight_break,
                fuel_pct=fuel_pct,
                zone=zone,
            )
        )


def _apply_zone_x(fuel_pct: float) -> None:
    op.execute(
        text(
            "UPDATE hotshot_rates "
            "SET fuel_pct = :fuel_pct "
            "WHERE UPPER(zone) = 'X' AND rate_set = 'default'"
        ).bindparams(fuel_pct=fuel_pct)
    )


def upgrade() -> None:
    _apply_zones_a_to_j(_FSI_RATES)
    _apply_zone_x(_ZONE_X_FUEL_FSI)


def downgrade() -> None:
    _apply_zones_a_to_j(_PRE_FSI_RATES)
    _apply_zone_x(_ZONE_X_FUEL_PRE)
