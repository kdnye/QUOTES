"""Shared utilities for CSV-driven bulk inserts.

Previously these helpers lived in ``scripts/import_air_rates.py``,
which left production code (``app/admin.py``,
``app/science_care/routes.py``) importing from a script module. Moving
them here lets the script keep doing its job while production paths
import from a stable service location.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Sequence, Tuple, Type, TypeVar

from sqlalchemy.orm import Session as SASession

from app.models import RateUpload

logger = logging.getLogger(__name__)

T = TypeVar("T")


def save_unique(
    session: SASession,
    model: Type[T],
    objects: Iterable[T],
    unique_attr: str | Sequence[str],
) -> Tuple[int, int]:
    """Persist only new records for a given model.

    Loads all existing keys up front and keeps an in-memory set to
    check for duplicates. Avoids issuing a ``SELECT`` query for every
    row, dramatically speeding up bulk imports such as the 28k ZIP
    codes in the original rate workbook.

    Args:
        session: Active :class:`sqlalchemy.orm.Session` used for DB I/O.
        model: SQLAlchemy model class to query for existing rows.
        objects: Iterable of model instances to insert.
        unique_attr: Model attribute (or sequence of attributes) that
            uniquely identifies a row.

    Returns:
        ``(inserted, skipped)`` counts.
    """

    attrs = (unique_attr,) if isinstance(unique_attr, str) else tuple(unique_attr)
    objs = list(objects)
    query_columns = [getattr(model, attr) for attr in attrs]
    rows = session.query(*query_columns).all()
    existing_keys = {
        row if len(attrs) > 1 else row[0] for row in rows  # type: ignore[index]
    }
    to_insert: List[T] = []
    inserted = 0
    skipped = 0
    for obj in objs:
        values = tuple(getattr(obj, attr) for attr in attrs)
        key = values if len(attrs) > 1 else values[0]
        key_display = key[0] if isinstance(key, tuple) else key
        if key in existing_keys:
            logger.info("Skipped existing %s: %s", model.__name__, key_display)
            skipped += 1
        else:
            logger.info("Inserted %s: %s", model.__name__, key_display)
            existing_keys.add(key)
            to_insert.append(obj)
            inserted += 1
    if to_insert:
        session.bulk_save_objects(to_insert)
    return inserted, skipped


def record_rate_upload(
    session: SASession, table_name: str, filename: str
) -> RateUpload:
    """Append a :class:`RateUpload` audit row to ``session`` and return it.

    Both the global admin CSV upload path
    (:func:`app.admin.upload_csv`) and the SC CSV upload path
    (:func:`app.science_care.routes.sc_upload_csv`) write an audit
    record after every successful upload. Centralising the construction
    eliminates the drift risk of one site evolving the payload
    independently of the other.

    The caller is responsible for committing the session. This helper
    only adds the row, matching the existing inline pattern.
    """

    audit_row = RateUpload(table_name=table_name, filename=filename)
    session.add(audit_row)
    return audit_row
