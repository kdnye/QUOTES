"""add multi_reference to sc_quote_sessions

Revision ID: c2d3e4f5a6b7
Revises: b1f3a4d5c6e7
Create Date: 2026-06-22 19:00:00.000000

Stamps a unified reference number (``SCMQ0001``-style by default, or a
customer-supplied string) across every leg of a multi-leg Science Care
submission. Indexed UNIQUE so the booking-email and lookup endpoints
can resolve a session in a single query.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1f3a4d5c6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sc_quote_sessions",
        sa.Column("multi_reference", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_sc_quote_sessions_multi_reference",
        "sc_quote_sessions",
        ["multi_reference"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sc_quote_sessions_multi_reference",
        table_name="sc_quote_sessions",
    )
    op.drop_column("sc_quote_sessions", "multi_reference")
