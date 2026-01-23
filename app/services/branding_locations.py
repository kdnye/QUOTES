"""Helpers for persisting per-rate-set GCS logo locations."""

from __future__ import annotations

from typing import List

from app.models import BrandLogoLocation, db
from app.services.rate_sets import normalize_rate_set


def get_brand_logo_location(rate_set: str) -> BrandLogoLocation | None:
    """Return the persisted logo location for ``rate_set`` when present.

    Args:
        rate_set: Rate set identifier to query.

    Returns:
        Matching :class:`app.models.BrandLogoLocation` row or ``None``.

    External dependencies:
        * Calls :func:`app.services.rate_sets.normalize_rate_set` to normalize
          the identifier.
        * Uses :class:`app.models.BrandLogoLocation` for the query.
    """

    normalized = normalize_rate_set(rate_set)
    return BrandLogoLocation.query.filter_by(rate_set=normalized).one_or_none()


def upsert_brand_logo_location(
    rate_set: str, gcs_bucket_location: str
) -> BrandLogoLocation:
    """Create or update the logo location for ``rate_set``.

    Args:
        rate_set: Rate set identifier to persist.
        gcs_bucket_location: GCS bucket location in ``gs://bucket/path`` format.

    Returns:
        The persisted :class:`app.models.BrandLogoLocation` row.

    External dependencies:
        * Calls :func:`app.services.rate_sets.normalize_rate_set` to normalize
          the identifier.
        * Uses :class:`app.models.BrandLogoLocation` for persistence.
        * Writes through :data:`app.models.db.session`.
    """

    normalized = normalize_rate_set(rate_set)
    record = BrandLogoLocation.query.filter_by(rate_set=normalized).one_or_none()
    if record is None:
        record = BrandLogoLocation(
            rate_set=normalized, gcs_bucket_location=gcs_bucket_location
        )
        db.session.add(record)
    else:
        record.gcs_bucket_location = gcs_bucket_location
    return record


def delete_brand_logo_location(rate_set: str) -> bool:
    """Remove the persisted logo location for ``rate_set`` when present.

    Args:
        rate_set: Rate set identifier to delete.

    Returns:
        ``True`` when a record was deleted, otherwise ``False``.

    External dependencies:
        * Calls :func:`app.services.rate_sets.normalize_rate_set` to normalize
          the identifier.
        * Uses :class:`app.models.BrandLogoLocation` for deletion.
        * Writes through :data:`app.models.db.session`.
    """

    normalized = normalize_rate_set(rate_set)
    record = BrandLogoLocation.query.filter_by(rate_set=normalized).one_or_none()
    if record is None:
        return False
    db.session.delete(record)
    return True


def list_brand_logo_locations() -> List[BrandLogoLocation]:
    """Return all persisted logo locations ordered by rate set.

    Returns:
        List of :class:`app.models.BrandLogoLocation` rows.

    External dependencies:
        * Uses :class:`app.models.BrandLogoLocation` for the query.
    """

    return BrandLogoLocation.query.order_by(BrandLogoLocation.rate_set.asc()).all()
