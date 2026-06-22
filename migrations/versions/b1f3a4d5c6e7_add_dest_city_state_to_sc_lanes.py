"""add dest_city and dest_state to sc_established_lanes

Revision ID: b1f3a4d5c6e7
Revises: a8b9c0d1e2f3
Create Date: 2026-06-22 18:00:00.000000

The SC workbook keys its established-lane VLOOKUP by ``lab_code + "City,State"``,
so a leg to any ZIP in (say) Mahwah, NJ picks up the SCPA → Mahwah lane price.
The first iteration of the SC schema mapped each lane row to a single
representative ``dest_zip``, which meant a different ZIP in the same metro
silently lost the established rate.

These two nullable columns let an admin upload a lane keyed by metro instead
of (or in addition to) a single ZIP. When both ``dest_city`` and ``dest_state``
are set on a row, the SC quote service will match it for any leg whose
``dest_zip`` resolves to that city/state via the workbook's
``Zipcode_Zones.csv`` reference. Existing ZIP-keyed rows keep working - the
ZIP match is still tried first.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1f3a4d5c6e7"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sc_established_lanes",
        sa.Column("dest_city", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "sc_established_lanes",
        sa.Column("dest_state", sa.String(length=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sc_established_lanes", "dest_state")
    op.drop_column("sc_established_lanes", "dest_city")
