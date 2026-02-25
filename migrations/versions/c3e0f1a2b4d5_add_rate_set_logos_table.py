"""add rate set logos table

Revision ID: c3e0f1a2b4d5
Revises: 6d5f7a8b9c10
Create Date: 2026-02-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3e0f1a2b4d5"
down_revision: Union[str, Sequence[str], None] = "6d5f7a8b9c10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``rate_set_logos`` table used by customer branding."""

    op.create_table(
        "rate_set_logos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rate_set", sa.String(length=50), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rate_set_logos_rate_set"),
        "rate_set_logos",
        ["rate_set"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the ``rate_set_logos`` table and its unique index."""

    op.drop_index(op.f("ix_rate_set_logos_rate_set"), table_name="rate_set_logos")
    op.drop_table("rate_set_logos")
