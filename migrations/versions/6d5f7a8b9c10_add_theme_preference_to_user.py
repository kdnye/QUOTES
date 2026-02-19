"""add theme_preference to user

Revision ID: 6d5f7a8b9c10
Revises: 84eccfd5f119
Create Date: 2026-02-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6d5f7a8b9c10"
down_revision: Union[str, Sequence[str], None] = "84eccfd5f119"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "users",
        sa.Column(
            "theme_preference",
            sa.String(length=10),
            nullable=False,
            server_default="auto",
        ),
    )
    op.alter_column("users", "theme_preference", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_column("users", "theme_preference")
