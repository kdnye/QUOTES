"""add booking_intake_json to sc_quote_sessions

Revision ID: e1f2a3b4c5d6
Revises: d9a8b7c6e5f4
Create Date: 2026-06-23 23:30:00.000000

Persists the shipper / consignee / pickup-date / delivery-date intake
form that the SC composer now collects on
``/sc/quote/<id>/email-ops/intake`` before showing the booking email
preview. Single JSON column following the same pattern as
``payload_json`` / ``boxes_json`` / ``consumables_json`` - the
schema can evolve without a follow-up migration each time the intake
form gains a field. ``NULL`` until the user submits the intake form
for the first time.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d9a8b7c6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sc_quote_sessions",
        sa.Column("booking_intake_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sc_quote_sessions", "booking_intake_json")
