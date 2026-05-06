"""Add api_key, api_enabled, api_approved to user table.

Revision ID: a9b1c2d3e4f5
Revises: f8a2c1d3e4b5
Create Date: 2026-05-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "f8a2c1d3e4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("api_approved", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("api_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("api_key", sa.String(128), nullable=True))
    op.create_index("ix_users_api_key", "users", ["api_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_api_key", table_name="users")
    op.drop_column("users", "api_key")
    op.drop_column("users", "api_enabled")
    op.drop_column("users", "api_approved")
