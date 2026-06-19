"""add consumables_json to sc_quote_session_legs

Revision ID: e3c4d5f6a7b8
Revises: d2b3c4e5f6a7
Create Date: 2026-06-19 22:00:00.000000

Per-leg consumable Qty selection on the SC quote form needs a place
to remember what the user picked. Storing a small JSON map on the
existing leg row keeps the read path simple (the audit replay path
loads the whole leg as a unit) and avoids a join table for only five
keys per row.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3c4d5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "d2b3c4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sc_quote_session_legs",
        sa.Column("consumables_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sc_quote_session_legs", "consumables_json")
