"""merge vsc_zones branch with previous heads

Revision ID: d4e5f6a7b8c9
Revises: b124ecb0f497, 9a7c2d1e4f90
Create Date: 2026-05-05 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = ("b124ecb0f497", "9a7c2d1e4f90")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
