"""add client_reference to quotes

Revision ID: a91c4d7e2b11
Revises: f7b3c9d2e1a4
Create Date: 2026-05-06 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a91c4d7e2b11"
down_revision = "f7b3c9d2e1a4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("quotes")}

    if "client_reference" not in existing_columns:
        op.add_column(
            "quotes",
            sa.Column("client_reference", sa.String(length=64), nullable=True),
        )

    indexes = {index["name"] for index in inspector.get_indexes("quotes")}
    if "ix_quotes_client_reference" not in indexes:
        op.create_index(
            op.f("ix_quotes_client_reference"),
            "quotes",
            ["client_reference"],
            unique=False,
        )

    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("quotes")
    }
    if "uq_quotes_user_id_client_reference" not in unique_constraints:
        op.create_unique_constraint(
            "uq_quotes_user_id_client_reference",
            "quotes",
            ["user_id", "client_reference"],
        )


def downgrade():
    op.drop_constraint("uq_quotes_user_id_client_reference", "quotes", type_="unique")
    op.drop_index(op.f("ix_quotes_client_reference"), table_name="quotes")
    op.drop_column("quotes", "client_reference")
