"""merge client_reference branch with vsc_zones head

Revision ID: f8a2c1d3e4b5
Revises: d4e5f6a7b8c9, a91c4d7e2b11
Create Date: 2026-05-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8a2c1d3e4b5"
down_revision: Union[str, Sequence[str], None] = ("d4e5f6a7b8c9", "a91c4d7e2b11")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
