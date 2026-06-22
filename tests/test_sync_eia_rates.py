"""Tests for strict EIA payload validation used by sync_eia_rates script."""

from datetime import datetime, timedelta

import pytest
from flask import Flask

from app import create_app
from app.models import FuelSurcharge, db
from scripts.sync_eia_rates import extract_latest_point, upsert_region_rate


class _UpsertTestConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Construct a Flask app bound to the PostgreSQL test database."""

    _UpsertTestConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(_UpsertTestConfig)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_extract_latest_point_returns_normalized_date_and_float_rate() -> None:
    """Return period and float when payload has expected structure."""

    payload = {
        "response": {
            "data": [
                {"period": "2026-04-20", "value": "3.891"},
            ]
        }
    }

    period, rate = extract_latest_point(payload, series_id="SERIES.ONE")

    assert period == "2026-04-20"
    assert rate == 3.891


def test_extract_latest_point_rejects_missing_data_list() -> None:
    """Raise ValueError when API payload omits required data list."""

    payload = {"response": {}}

    try:
        extract_latest_point(payload, series_id="SERIES.MISSING")
        assert False, "Expected ValueError for missing data list"
    except ValueError as exc:
        assert "missing 'data' list" in str(exc)


def test_extract_latest_point_rejects_empty_data() -> None:
    """Raise ValueError when API payload data list is empty."""

    payload = {"response": {"data": []}}

    try:
        extract_latest_point(payload, series_id="SERIES.EMPTY")
        assert False, "Expected ValueError for empty data"
    except ValueError as exc:
        assert "data list is empty" in str(exc)


def test_extract_latest_point_rejects_non_numeric_value() -> None:
    """Raise ValueError when API value cannot be converted to float."""

    payload = {"response": {"data": [{"period": "2026-04", "value": "n/a"}]}}

    try:
        extract_latest_point(payload, series_id="SERIES.BADVAL")
        assert False, "Expected ValueError for non-numeric value"
    except ValueError as exc:
        assert "non-numeric" in str(exc)


def test_upsert_region_rate_bumps_last_updated_when_rate_unchanged(
    app: Flask,
) -> None:
    """Force ``last_updated`` to advance on every sync, even no-op rate writes.

    ``FuelSurcharge.last_updated`` is the authoritative "last successful
    pull" indicator on the snapshot view. EIA frequently returns the same
    weekly value across multiple sync runs; without an explicit bump the
    snapshot would still appear stale.
    """

    stale = datetime.utcnow() - timedelta(days=45)
    db.session.add(
        FuelSurcharge(padd_region="PADD1", current_rate=3.872, last_updated=stale)
    )
    db.session.commit()

    upsert_region_rate("PADD1", 3.872)
    db.session.commit()

    row = FuelSurcharge.query.filter_by(padd_region="PADD1").one()
    assert row.current_rate == 3.872
    assert row.last_updated > stale
    assert datetime.utcnow() - row.last_updated < timedelta(minutes=5)


def test_upsert_region_rate_inserts_new_row_with_current_last_updated(
    app: Flask,
) -> None:
    """Brand-new region rows must be timestamped at insert time."""

    upsert_region_rate("PADD2", 3.55)
    db.session.commit()

    row = FuelSurcharge.query.filter_by(padd_region="PADD2").one()
    assert row.current_rate == 3.55
    assert row.last_updated is not None
    assert datetime.utcnow() - row.last_updated < timedelta(minutes=5)
