"""Unit tests for app.services.bulk_import.

Covers the two helpers that production code now imports from this
module instead of from ``scripts/import_air_rates.py``:

* ``save_unique`` — bulk-insert helper with in-memory dedup.
* ``record_rate_upload`` — RateUpload audit-row builder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.models import RateUpload, ZipZone, db  # noqa: E402
from app.services.bulk_import import (  # noqa: E402
    record_rate_upload,
    save_unique,
)


class TestBulkImportConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestBulkImportConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestBulkImportConfig)
    with app.app_context():
        yield app
        db.session.remove()
        # Reflect before drop_all so alembic_version (created via raw
        # SQL, not in db.Model's metadata) gets cleaned up too. Without
        # this a reused test DB carries the version row forward and
        # subsequent migrations no-op while the actual tables are gone.
        db.metadata.reflect(bind=db.engine)
        db.drop_all()


def test_save_unique_inserts_new_rows(app: Flask) -> None:
    inserted, skipped = save_unique(
        db.session,
        ZipZone,
        [
            ZipZone(zipcode="85705", dest_zone=1, rate_set="default"),
            ZipZone(zipcode="60601", dest_zone=2, rate_set="default"),
        ],
        unique_attr="zipcode",
    )
    db.session.commit()

    assert inserted == 2
    assert skipped == 0
    assert ZipZone.query.filter_by(zipcode="85705").one().dest_zone == 1


def test_save_unique_skips_existing(app: Flask) -> None:
    db.session.add(ZipZone(zipcode="85705", dest_zone=1, rate_set="default"))
    db.session.commit()

    inserted, skipped = save_unique(
        db.session,
        ZipZone,
        [
            ZipZone(zipcode="85705", dest_zone=99, rate_set="default"),
            ZipZone(zipcode="60601", dest_zone=2, rate_set="default"),
        ],
        unique_attr="zipcode",
    )
    db.session.commit()

    assert inserted == 1
    assert skipped == 1
    # The duplicate didn't overwrite the original dest_zone.
    assert ZipZone.query.filter_by(zipcode="85705").one().dest_zone == 1


def test_save_unique_compound_key(app: Flask) -> None:
    # (rate_set, zipcode) compound key - same zipcode under different
    # rate_set should both insert.
    inserted, skipped = save_unique(
        db.session,
        ZipZone,
        [
            ZipZone(zipcode="85705", dest_zone=1, rate_set="default"),
            ZipZone(zipcode="85705", dest_zone=9, rate_set="science_care"),
        ],
        unique_attr=("rate_set", "zipcode"),
    )
    db.session.commit()

    assert inserted == 2
    assert skipped == 0


def test_save_unique_empty_input_skips_query(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When there's nothing to insert, the helper must not issue the
    # existing-keys SELECT - that's a needless full-table read for
    # callers who happened to feed in a filtered-empty iterable.
    called = {"queries": 0}
    original_query = db.session.query

    def counting_query(*args, **kwargs):
        called["queries"] += 1
        return original_query(*args, **kwargs)

    monkeypatch.setattr(db.session, "query", counting_query)

    inserted, skipped = save_unique(
        db.session, ZipZone, [], unique_attr="zipcode"
    )

    assert (inserted, skipped) == (0, 0)
    assert called["queries"] == 0


def test_save_unique_compound_key_log_includes_all_parts(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    # The compound-key log line must distinguish between rows that
    # share their first attribute (e.g. multiple rate_set="default"
    # rows would otherwise all log the same key_display).
    with caplog.at_level("INFO", logger="app.services.bulk_import"):
        save_unique(
            db.session,
            ZipZone,
            [
                ZipZone(zipcode="85705", dest_zone=1, rate_set="default"),
                ZipZone(zipcode="60601", dest_zone=2, rate_set="default"),
            ],
            unique_attr=("rate_set", "zipcode"),
        )
        db.session.commit()

    messages = [r.getMessage() for r in caplog.records]
    assert any("default-85705" in m for m in messages), messages
    assert any("default-60601" in m for m in messages), messages


def test_record_rate_upload_adds_row(app: Flask) -> None:
    row = record_rate_upload(
        db.session, table_name="zip_zones", filename="zips_2026_06.csv"
    )
    db.session.commit()

    assert row.id is not None
    fetched = RateUpload.query.filter_by(table_name="zip_zones").one()
    assert fetched.filename == "zips_2026_06.csv"


def test_scripts_import_air_rates_reexports_save_unique() -> None:
    # The script keeps re-exporting save_unique for any external
    # caller that imported it from the old location. Verify both
    # paths point at the same callable.
    from app.services import bulk_import
    from scripts import import_air_rates

    assert import_air_rates.save_unique is bulk_import.save_unique
