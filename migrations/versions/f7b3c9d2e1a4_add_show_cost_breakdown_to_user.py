"""add show_cost_breakdown to user

Revision ID: f7b3c9d2e1a4
Revises: 84eccfd5f119
Create Date: 2026-05-01 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7b3c9d2e1a4'
down_revision = '84eccfd5f119'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'users',
        sa.Column('show_cost_breakdown', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column('users', 'show_cost_breakdown')
