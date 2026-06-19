"""Unit tests for query_with_rate_set_fallback.

The helper underpins five lookup functions in app/quote/logic_air.py.
These tests pin its behavior so a future refactor of the helper itself
can't silently change the fallback policy across all of them.
"""

from __future__ import annotations

import pytest
from flask import Flask

from app import create_app
from app.models import RATE_SET_DEFAULT, ZipZone, db
from app.services.rate_sets import (
    DEFAULT_RATE_SET,
    query_with_rate_set_fallback,
)


class TestRateSetFallbackConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestRateSetFallbackConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestRateSetFallbackConfig)
    with app.app_context():
        yield app
        # Reflect before dropping so the Alembic-managed `alembic_version`
        # table (created via raw SQL, not in db.Model's metadata) is also
        # cleaned up. Without this, a reused test DB would carry the
        # version row forward and subsequent migrations would no-op while
        # the actual rate tables are gone - leading to ProgrammingError
        # in unrelated tests.
        db.session.remove()
        db.metadata.reflect(bind=db.engine)
        db.drop_all()


def _seed_zip_zone(
    zipcode: str, rate_set: str, dest_zone: int = 1
) -> ZipZone:
    row = ZipZone(zipcode=zipcode, dest_zone=dest_zone, rate_set=rate_set)
    db.session.add(row)
    db.session.commit()
    return row


def test_returns_row_for_requested_rate_set(app: Flask) -> None:
    _seed_zip_zone("85705", rate_set="science_care", dest_zone=42)

    row = query_with_rate_set_fallback(
        db.session, ZipZone, "science_care", zipcode="85705"
    )

    assert row is not None
    assert row.dest_zone == 42
    assert row.rate_set == "science_care"


def test_falls_back_to_default_when_requested_misses(app: Flask) -> None:
    _seed_zip_zone("85705", rate_set=DEFAULT_RATE_SET, dest_zone=7)

    row = query_with_rate_set_fallback(
        db.session, ZipZone, "science_care", zipcode="85705"
    )

    assert row is not None
    assert row.rate_set == DEFAULT_RATE_SET
    assert row.dest_zone == 7


def test_returns_none_when_neither_matches(app: Flask) -> None:
    row = query_with_rate_set_fallback(
        db.session, ZipZone, "science_care", zipcode="00000"
    )
    assert row is None


def test_does_not_cycle_back_when_rate_set_is_already_default(
    app: Flask,
) -> None:
    # Only a science_care row exists.
    _seed_zip_zone("85705", rate_set="science_care", dest_zone=3)

    # Asking for the DEFAULT rate-set must NOT silently surface the
    # science_care row - tenant isolation depends on this.
    row = query_with_rate_set_fallback(
        db.session, ZipZone, DEFAULT_RATE_SET, zipcode="85705"
    )

    assert row is None


def test_multiple_kwargs_filter(app: Flask) -> None:
    _seed_zip_zone("85705", rate_set="science_care", dest_zone=1)
    _seed_zip_zone("85706", rate_set="science_care", dest_zone=2)

    row = query_with_rate_set_fallback(
        db.session, ZipZone, "science_care", zipcode="85706"
    )

    assert row is not None
    assert row.dest_zone == 2


def test_default_rate_set_constant_aliases_to_models_constant() -> None:
    # Pinned to catch any future drift between the two historical
    # spellings - the consolidation done in PR #263.
    assert DEFAULT_RATE_SET == RATE_SET_DEFAULT
