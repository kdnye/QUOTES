"""add fuel surcharges table

Revision ID: e5a1d2c3f4b6
Revises: c3e0f1a2b4d5
Create Date: 2026-04-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5a1d2c3f4b6"
down_revision: Union[str, Sequence[str], None] = "c3e0f1a2b4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``fuel_surcharges`` table with a unique PADD region."""

    op.create_table(
        "fuel_surcharges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("padd_region", sa.String(length=50), nullable=False),
        sa.Column("current_rate", sa.Float(), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fuel_surcharges_padd_region"),
        "fuel_surcharges",
        ["padd_region"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the ``fuel_surcharges`` table and its unique index."""

    op.drop_index(op.f("ix_fuel_surcharges_padd_region"), table_name="fuel_surcharges")
    op.drop_table("fuel_surcharges")
