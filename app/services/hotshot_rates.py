"""Helpers for retrieving Hotshot rate data from the database."""

from contextlib import contextmanager
from typing import Generator

from flask import has_app_context
from sqlalchemy.orm import Session as SASession

from app.models import RATE_SET_DEFAULT, db, HotshotRate
from app.database import Session


@contextmanager
def _session_scope() -> Generator[SASession, None, None]:
    """Yield a SQLAlchemy session tied to the active application.

    Falls back to the legacy standalone :class:`~sqlalchemy.orm.Session`
    when no Flask application context is available. This design prevents
    "no such table" errors when the legacy session is bound to a different
    engine than the Flask app, such as when a standalone worker connects with
    its own engine configuration.

    Args:
        None.

    Returns:
        Generator[:class:`sqlalchemy.orm.Session`, None, None]: Context manager
        yielding an active SQLAlchemy session.

    External dependencies:
        * Calls :func:`flask.has_app_context` to detect a Flask context.
        * Uses :data:`app.models.db.session` for Flask-managed sessions.
        * Instantiates :data:`app.database.Session` for standalone sessions.
    """

    if has_app_context():
        yield db.session  # type: ignore[generator-type]
    else:
        session = Session()
        try:
            yield session
        finally:
            session.close()


def get_hotshot_zone_by_miles(miles: float, *, rate_set: str = RATE_SET_DEFAULT) -> str:
    """Return the zone corresponding to the given mileage.

    Args:
        miles: Distance of the shipment.

    Args:
        miles: Distance of the shipment.
        rate_set: Identifier for the desired rate table. Defaults to
            :data:`app.models.RATE_SET_DEFAULT`.

    Returns:
        The zone code matching ``miles``. If no row in the selected
        :class:`~app.models.HotshotRate` set covers the requested distance,
        ``"X"`` is returned. Zone ``"X"`` must exist in the database to
        provide a fallback rate.
    """

    with _session_scope() as session:
        record = (
            session.query(HotshotRate)
            .filter(HotshotRate.rate_set == rate_set, HotshotRate.miles >= miles)
            .order_by(HotshotRate.miles)
            .first()
        )
        if record is None and rate_set != RATE_SET_DEFAULT:
            record = (
                session.query(HotshotRate)
                .filter(
                    HotshotRate.rate_set == RATE_SET_DEFAULT,
                    HotshotRate.miles >= miles,
                )
                .order_by(HotshotRate.miles)
                .first()
            )
        return record.zone if record is not None else "X"


def get_current_hotshot_rate(
    zone: str, *, rate_set: str = RATE_SET_DEFAULT
) -> HotshotRate:
    """Return the rate information for a given zone.

    Args:
        zone: Zone code to look up.
        rate_set: Identifier for the rate table to query.

    Raises:
        ValueError: If the zone does not exist in the table.
    """

    with _session_scope() as session:
        record = (
            session.query(HotshotRate)
            .filter(HotshotRate.zone == zone, HotshotRate.rate_set == rate_set)
            .order_by(HotshotRate.miles)
            .first()
        )
        if record is None and rate_set != RATE_SET_DEFAULT:
            record = (
                session.query(HotshotRate)
                .filter(
                    HotshotRate.zone == zone, HotshotRate.rate_set == RATE_SET_DEFAULT
                )
                .order_by(HotshotRate.miles)
                .first()
            )
        if record is None:
            raise ValueError(f"Hotshot rate not found for zone {zone}")
        return record
