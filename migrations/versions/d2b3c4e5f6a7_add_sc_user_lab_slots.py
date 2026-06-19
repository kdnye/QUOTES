"""add sc_user_lab_slots

Revision ID: d2b3c4e5f6a7
Revises: c1a2d3e4f5b6
Create Date: 2026-06-19 19:00:00.000000

Per-user default labs for the seven SC quote slots. The SC quote page
prefills each ``lab_code_<n>`` input from these rows so sales reps
don't have to retype the same seven labs on every visit.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2b3c4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "c1a2d3e4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sc_user_lab_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("lab_code", sa.String(length=20), nullable=False),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set",
            "user_id",
            "leg_index",
            name="uq_sc_user_lab_slots_rate_set_user_leg",
        ),
    )
    op.create_index(
        op.f("ix_sc_user_lab_slots_user_id"),
        "sc_user_lab_slots",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sc_user_lab_slots_rate_set"),
        "sc_user_lab_slots",
        ["rate_set"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sc_user_lab_slots_rate_set"),
        table_name="sc_user_lab_slots",
    )
    op.drop_index(
        op.f("ix_sc_user_lab_slots_user_id"),
        table_name="sc_user_lab_slots",
    )
    op.drop_table("sc_user_lab_slots")
