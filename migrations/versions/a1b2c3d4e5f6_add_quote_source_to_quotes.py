"""add quote_source to quotes

Revision ID: a1b2c3d4e5f6
Revises: f8a2c1d3e4b5
Create Date: 2026-05-07 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "a1b2c3d4e5f6"
down_revision = "f8a2c1d3e4b5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("quotes")}
    if "quote_source" not in existing_columns:
        op.add_column(
            "quotes",
            sa.Column("quote_source", sa.String(20), nullable=True),
        )
        op.create_index("ix_quotes_quote_source", "quotes", ["quote_source"])


def downgrade():
    op.drop_index("ix_quotes_quote_source", table_name="quotes")
    op.drop_column("quotes", "quote_source")
