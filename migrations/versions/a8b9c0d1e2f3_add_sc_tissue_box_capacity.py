"""add sc_tissue_box_capacity, SMALL_AIRTRAY box type, backfill capacities

Revision ID: a8b9c0d1e2f3
Revises: f4d5e6a7b8c9
Create Date: 2026-06-22 12:00:00.000000

Refines tissue handling to match the customer-supplied template: one
``pieces_per_box`` value per (tissue, box) pair instead of a single
``default_box_type_code`` + ``pieces_per_box`` on :class:`SCTissueCode`.

Schema changes:
* Add ``sc_tissue_box_capacity`` join table.
* Add ``SMALL_AIRTRAY`` row to ``sc_box_types`` (dimensions are placeholder
  zeros so an SC admin must edit before quoting routes use it - the
  allocator skips a box with no interior dims).

Data backfill:
* Copy each existing ``(tissue_code, default_box_type_code, pieces_per_box)``
  triple from ``sc_tissue_codes`` into a capacity row so the new allocator
  produces the same picks for legacy data until a fresh CSV is uploaded.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f4d5e6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sc_tissue_box_capacity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tissue_code", sa.String(length=40), nullable=False),
        sa.Column("box_code", sa.String(length=20), nullable=False),
        sa.Column("pieces_per_box", sa.Integer(), nullable=False),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set",
            "tissue_code",
            "box_code",
            name="uq_sc_tissue_box_capacity_rate_set_tissue_box",
        ),
    )
    op.create_index(
        op.f("ix_sc_tissue_box_capacity_rate_set"),
        "sc_tissue_box_capacity",
        ["rate_set"],
        unique=False,
    )

    # SMALL_AIRTRAY is a new fifth box type. Placeholder dimensions of
    # zero force an SC admin to edit the row before quoting uses it - the
    # allocator skips zero-volume boxes. Tare is left at zero for the
    # same reason.
    op.execute(
        sa.text(
            """
            INSERT INTO sc_box_types (
                code, label, length_in, width_in, height_in,
                tare_weight_lb, max_payload_lb, rate_set
            )
            SELECT
                'SMALL_AIRTRAY',
                'Small Airtray',
                0, 0, 0, 0, NULL, 'science_care'
            WHERE NOT EXISTS (
                SELECT 1 FROM sc_box_types
                WHERE rate_set = 'science_care'
                  AND code = 'SMALL_AIRTRAY'
            )
            """
        )
    )

    # Backfill capacities from legacy (default_box_type_code, pieces_per_box)
    # pairs so existing tenants keep producing the same box picks until a
    # fresh CSV is uploaded. Rows where either value is NULL/zero/blank are
    # left out - the allocator will then refuse to allocate boxes for that
    # tissue, surfacing the missing-data error rather than silently picking
    # an arbitrary box.
    op.execute(
        sa.text(
            """
            INSERT INTO sc_tissue_box_capacity (
                tissue_code, box_code, pieces_per_box, rate_set
            )
            SELECT
                tc.tissue_code,
                tc.default_box_type_code,
                tc.pieces_per_box,
                tc.rate_set
            FROM sc_tissue_codes AS tc
            WHERE tc.default_box_type_code IS NOT NULL
              AND tc.default_box_type_code <> ''
              AND tc.pieces_per_box IS NOT NULL
              AND tc.pieces_per_box > 0
              AND NOT EXISTS (
                  SELECT 1 FROM sc_tissue_box_capacity AS cap
                  WHERE cap.rate_set = tc.rate_set
                    AND cap.tissue_code = tc.tissue_code
                    AND cap.box_code = tc.default_box_type_code
              )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM sc_box_types
            WHERE rate_set = 'science_care'
              AND code = 'SMALL_AIRTRAY'
            """
        )
    )
    op.drop_index(
        op.f("ix_sc_tissue_box_capacity_rate_set"),
        table_name="sc_tissue_box_capacity",
    )
    op.drop_table("sc_tissue_box_capacity")
