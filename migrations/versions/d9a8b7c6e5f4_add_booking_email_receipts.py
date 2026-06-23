"""add booking_email_receipts audit table

Revision ID: d9a8b7c6e5f4
Revises: c8f1a3b6d92e
Create Date: 2026-06-23 22:30:00.000000

Persists a per-send audit row for every Postmark/SMTP booking email
dispatched from either composer page (``/sc/quote/<id>/email-ops`` or
``/quotes/<id>/email``). Lets the lookup page render "last sent at X
by Y" and gives ops a paper trail without re-querying the upstream
SC session or Quote row.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9a8b7c6e5f4"
down_revision: Union[str, Sequence[str], None] = "c8f1a3b6d92e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_email_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column(
            "sender_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("to_addr", sa.String(length=255), nullable=False),
        sa.Column("cc_addr", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "postmark_message_id", sa.String(length=120), nullable=True
        ),
    )
    op.create_index(
        op.f("ix_booking_email_receipts_kind"),
        "booking_email_receipts",
        ["kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_booking_email_receipts_reference"),
        "booking_email_receipts",
        ["reference"],
        unique=False,
    )
    op.create_index(
        op.f("ix_booking_email_receipts_sender_user_id"),
        "booking_email_receipts",
        ["sender_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_booking_email_receipts_sent_at"),
        "booking_email_receipts",
        ["sent_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_booking_email_receipts_status"),
        "booking_email_receipts",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_booking_email_receipts_status"),
        table_name="booking_email_receipts",
    )
    op.drop_index(
        op.f("ix_booking_email_receipts_sent_at"),
        table_name="booking_email_receipts",
    )
    op.drop_index(
        op.f("ix_booking_email_receipts_sender_user_id"),
        table_name="booking_email_receipts",
    )
    op.drop_index(
        op.f("ix_booking_email_receipts_reference"),
        table_name="booking_email_receipts",
    )
    op.drop_index(
        op.f("ix_booking_email_receipts_kind"),
        table_name="booking_email_receipts",
    )
    op.drop_table("booking_email_receipts")
