"""realign sc_accessorial_map names to Accessorial.name

Revision ID: b2e5c7d1a3f4
Revises: a1c4d6e8f2b9
Create Date: 2026-06-23 17:30:00.000000

``app.services.quote.create_quote`` resolves the labels emitted by
``app.services.science_care_quote._collect_accessorials`` against
``Accessorial.name`` via a stripped, lowercased lookup. Anything that
doesn't match is silently dropped, so an SC leg with accessorials
checked still came out priced as if none were selected.

The original ``rates/science_care/sc_accessorial_map.csv`` shipped with
``accessorial_name`` strings that didn't exist in
``rates/accessorial_cost.csv``:

    J3 PickUp 4 Hour Window (e.g 10:00-14:00) -> Accessorial '4hr Window'
    J4 Specific PickUp Time                   -> Accessorial 'Less than 4 hrs'
    J5 Delivery After Hours                   -> Accessorial 'After Hours'
    J7 Two Man Delivery                       -> Accessorial 'Two Man'
    J8 Liftgate Delivery                      -> Accessorial 'Liftgate'

This migration rewrites the names in place for the science_care
rate set. The CSV has been updated to match so a fresh import lands
on the same values.

Idempotent: each UPDATE narrows by the legacy name, so re-running is a
no-op once the rows are correct. The downgrade restores the legacy
strings for symmetry, but they remain broken under the runtime - we
keep the down path so a rollback can still ``alembic downgrade`` past
this revision.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b2e5c7d1a3f4"
down_revision: Union[str, Sequence[str], None] = "a1c4d6e8f2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RENAMES = [
    ("J3", "PickUp 4 Hour Window (e.g 10:00-14:00)", "4hr Window"),
    ("J4", "Specific PickUp Time", "Less than 4 hrs"),
    ("J5", "Delivery After Hours", "After Hours"),
    ("J7", "Two Man Delivery", "Two Man"),
    ("J8", "Liftgate Delivery", "Liftgate"),
]


# Scope the rewrite to the science_care rate set so a future tenant with
# its own form_field=J3 row can't get clobbered by this migration. The
# constant is duplicated here (rather than imported from app.models) so
# the migration stays runnable without the Flask app on the path.
_SC_RATE_SET = "science_care"

_UPDATE_SQL = sa.text(
    "UPDATE sc_accessorial_map "
    "SET accessorial_name = :new_name "
    "WHERE form_field = :form_field "
    "  AND accessorial_name = :old_name "
    "  AND rate_set = :rate_set"
)


def upgrade() -> None:
    bind = op.get_bind()
    for form_field, old_name, new_name in _RENAMES:
        bind.execute(
            _UPDATE_SQL,
            {
                "form_field": form_field,
                "old_name": old_name,
                "new_name": new_name,
                "rate_set": _SC_RATE_SET,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    for form_field, old_name, new_name in _RENAMES:
        bind.execute(
            _UPDATE_SQL,
            {
                "form_field": form_field,
                "old_name": new_name,
                "new_name": old_name,
                "rate_set": _SC_RATE_SET,
            },
        )
