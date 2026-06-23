"""backfill Zone X per_mile from hardcoded constant

Revision ID: a1c4d6e8f2b9
Revises: c2d3e4f5a6b7
Create Date: 2026-06-23 16:00:00.000000

Before this change, ``app/quote/logic_hotshot.py`` ignored
``HotshotRate.per_mile`` for Zone X rows and used a hardcoded
``ZONE_X_PER_MILE_RATE = 6.0192`` constant. The constant has been
removed; the runtime now reads ``per_mile`` from the rate row. This
migration backfills any Zone X row whose ``per_mile`` is NULL with
``6.0192`` so the day-one production behavior is identical to the
removed constant. Idempotent: re-running it is a no-op because the
WHERE clause matches only NULL rows.

The downgrade nulls Zone X ``per_mile`` back out, which would cause
Zone X quotes to raise ``ValueError`` until the rows are repopulated
- intentional, since the old constant-based code path is also gone.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a1c4d6e8f2b9"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE hotshot_rates SET per_mile = 6.0192 "
        "WHERE UPPER(zone) = 'X' AND per_mile IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE hotshot_rates SET per_mile = NULL "
        "WHERE UPPER(zone) = 'X' AND per_mile = 6.0192"
    )
