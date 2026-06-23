"""seed J6 Weekend Pickup/Delivery into sc_accessorial_map

Revision ID: c8f1a3b6d92e
Revises: b2e5c7d1a3f4
Create Date: 2026-06-23 18:30:00.000000

The original SC accessorial seed (``rates/science_care/sc_accessorial_map.csv``)
intentionally skipped J6 because the Excel macro had no API equivalent for
Weekend. ``Accessorial`` has always had a ``Weekend`` row at $125, so the
mapping was the only thing missing. This migration adds the J6 row for
the science_care rate set, and repoints any existing J6 row whose
``accessorial_name`` does not match ``Weekend`` to the canonical value.

Idempotent: the INSERT is guarded by a ``NOT EXISTS`` lookup on
``(rate_set, form_field)`` (which already has a UNIQUE constraint), and
the UPDATE only fires when ``accessorial_name`` is something other than
``Weekend``. The downgrade removes the row only when it still references
``Weekend``, so an admin who repointed it via the UI before the rollback
keeps their change.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c8f1a3b6d92e"
down_revision: Union[str, Sequence[str], None] = "b2e5c7d1a3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SC_RATE_SET = "science_care"
_FORM_FIELD = "J6"
_DISPLAY_LABEL = "Weekend Pickup/Delivery"
_ACCESSORIAL_NAME = "Weekend"


_INSERT_SQL = sa.text(
    "INSERT INTO sc_accessorial_map (form_field, display_label, accessorial_name, rate_set) "
    "SELECT :form_field, :display_label, :accessorial_name, :rate_set "
    "WHERE NOT EXISTS ("
    "  SELECT 1 FROM sc_accessorial_map "
    "  WHERE rate_set = :rate_set AND form_field = :form_field"
    ")"
)


_UPDATE_SQL = sa.text(
    "UPDATE sc_accessorial_map "
    "SET accessorial_name = :accessorial_name, display_label = :display_label "
    "WHERE rate_set = :rate_set "
    "  AND form_field = :form_field "
    "  AND accessorial_name <> :accessorial_name"
)


_DELETE_SQL = sa.text(
    "DELETE FROM sc_accessorial_map "
    "WHERE rate_set = :rate_set "
    "  AND form_field = :form_field "
    "  AND accessorial_name = :accessorial_name"
)


def upgrade() -> None:
    bind = op.get_bind()
    params = {
        "form_field": _FORM_FIELD,
        "display_label": _DISPLAY_LABEL,
        "accessorial_name": _ACCESSORIAL_NAME,
        "rate_set": _SC_RATE_SET,
    }
    bind.execute(_INSERT_SQL, params)
    bind.execute(_UPDATE_SQL, params)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        _DELETE_SQL,
        {
            "form_field": _FORM_FIELD,
            "accessorial_name": _ACCESSORIAL_NAME,
            "rate_set": _SC_RATE_SET,
        },
    )
