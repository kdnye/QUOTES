"""merge all current heads into a single head

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6, a9b1c2d3e4f5, c3e0f1a2b4d5
Create Date: 2026-05-07 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = (
    "a1b2c3d4e5f6",  # add_quote_source_to_quotes
    "a9b1c2d3e4f5",  # add_api_key_fields_to_user
    "c3e0f1a2b4d5",  # add_rate_set_logos_table
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
