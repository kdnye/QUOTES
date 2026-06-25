"""add email two-factor authentication

Adds the per-user ``two_factor_enabled`` toggle and the ``email_otp_tokens``
table backing the email one-time-code login challenge implemented in
``app/services/two_factor.py``.

Revision ID: b7e2c9a14d3f
Revises: f1d3b8c9e7a5
Create Date: 2026-06-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7e2c9a14d3f"
down_revision: Union[str, Sequence[str], None] = "f1d3b8c9e7a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing accounts default to 2FA on (server_default true). Drop the
    # server default afterwards so the application model owns the default for
    # new rows, mirroring the can_send_mail migration pattern.
    op.add_column(
        "users",
        sa.Column(
            "two_factor_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("users", "two_factor_enabled", server_default=None)

    op.create_table(
        "email_otp_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_otp_tokens_user_id"),
        "email_otp_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_email_otp_tokens_user_id"), table_name="email_otp_tokens"
    )
    op.drop_table("email_otp_tokens")
    op.drop_column("users", "two_factor_enabled")
