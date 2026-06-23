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

The downgrade is intentionally a no-op. Nulling rows whose ``per_mile``
matches the backfilled ``6.0192`` would also wipe out any intentional
admin edit that happens to set Zone X ``per_mile`` to ``6.0192``
(an entirely legitimate customer-specific value, since it's the seed
default). Data-only migrations like this one are conventionally
left in place on rollback - the schema is unchanged, and the old
code path that ignored ``per_mile`` for Zone X simply doesn't read
the column, so the populated cells are harmless under the older
runtime.
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
    pass
