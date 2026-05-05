"""add dedicated vsc_zones table

Revision ID: 9a7c2d1e4f90
Revises: f7b3c9d2e1a4
Create Date: 2026-05-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9a7c2d1e4f90"
down_revision = "f7b3c9d2e1a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vsc_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zipcode", sa.String(length=5), nullable=False),
        sa.Column("vsc_zone", sa.Integer(), nullable=False),
        sa.Column("rate_set", sa.String(length=50), nullable=False),
        sa.CheckConstraint("length(zipcode) = 5", name="ck_vsc_zones_zipcode_len_5"),
        sa.CheckConstraint("zipcode ~ '^[0-9]{5}$'", name="ck_vsc_zones_zipcode_digits"),
        sa.CheckConstraint("vsc_zone >= 1 AND vsc_zone <= 10", name="ck_vsc_zones_zone_range"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rate_set", "zipcode", name="uq_vsc_zones_rate_set_zipcode"),
    )
    op.create_index(op.f("ix_vsc_zones_rate_set"), "vsc_zones", ["rate_set"], unique=False)
    op.create_index("ix_vsc_zones_zipcode", "vsc_zones", ["zipcode"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vsc_zones_zipcode", table_name="vsc_zones")
    op.drop_index(op.f("ix_vsc_zones_rate_set"), table_name="vsc_zones")
    op.drop_table("vsc_zones")
