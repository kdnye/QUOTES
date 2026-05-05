"""merge conflicting heads

Revision ID: b124ecb0f497
Revises: e5a1d2c3f4b6, f7b3c9d2e1a4
Create Date: 2026-05-01 21:12:31.272278

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b124ecb0f497'
down_revision: Union[str, Sequence[str], None] = ('e5a1d2c3f4b6', 'f7b3c9d2e1a4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
