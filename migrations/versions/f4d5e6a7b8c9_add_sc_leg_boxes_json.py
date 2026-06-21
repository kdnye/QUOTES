"""add boxes_json to sc_quote_session_legs

Revision ID: f4d5e6a7b8c9
Revises: e3c4d5f6a7b8
Create Date: 2026-06-19 23:00:00.000000

Per-leg box-count overrides on the SC quote form need a place to
remember the final per-box-type counts (after applying any user
overrides). Same shape as consumables_json: a small JSON map on the
existing leg row keeps the read path simple and avoids a join table
for at most a handful of keys per row.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4d5e6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "e3c4d5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sc_quote_session_legs",
        sa.Column("boxes_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sc_quote_session_legs", "boxes_json")
