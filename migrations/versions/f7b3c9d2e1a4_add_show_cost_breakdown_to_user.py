"""add show_cost_breakdown to user

Revision ID: f7b3c9d2e1a4
Revises: e5a1d2c3f4b6
Create Date: 2026-05-01 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'f7b3c9d2e1a4'
down_revision = 'e5a1d2c3f4b6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col['name'] for col in inspector.get_columns('users')}
    if 'show_cost_breakdown' not in existing_columns:
        op.add_column(
            'users',
            sa.Column('show_cost_breakdown', sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    op.drop_column('users', 'show_cost_breakdown')
