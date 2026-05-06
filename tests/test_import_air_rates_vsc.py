from __future__ import annotations

import pandas as pd

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from scripts import import_air_rates


class _FakeVscZone:
    def __init__(self, zipcode: str, vsc_zone: int, rate_set: str):
        self.zipcode = zipcode
        self.vsc_zone = vsc_zone
        self.rate_set = rate_set


class _FakeVscZoneQuery:
    def __init__(self, rows: list[_FakeVscZone]):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, existing: list[_FakeVscZone] | None = None):
        self.existing = existing or []
        self.added: list[_FakeVscZone] = []

    def query(self, _model):
        return _FakeVscZoneQuery(self.existing)

    def add(self, row):
        self.added.append(row)


def test_load_vsc_zip_zones_happy_path() -> None:
    df = pd.DataFrame(
        {
            "Zipcode": ["85001-1234", "00901", "94105"],
            "Dest Zone": [1, 2, 10],
        }
    )

    rows, invalid_count = import_air_rates.load_vsc_zip_zones(df)

    assert invalid_count == 0
    assert {(r.zipcode, r.vsc_zone) for r in rows} == {
        ("85001", 1),
        ("00901", 2),
        ("94105", 10),
    }


def test_load_vsc_zip_zones_accepts_zone_column() -> None:
    """The actual vsc zones.csv uses 'Zone' not 'Dest Zone'."""
    df = pd.DataFrame(
        {
            "Zipcode": ["90808", "90045"],
            "City": ["LONG BEACH", "LOS ANGELES"],
            "State": ["CA", "CA"],
            "Zone": [9, 9],
        }
    )

    rows, invalid_count = import_air_rates.load_vsc_zip_zones(df)

    assert invalid_count == 0
    assert {(r.zipcode, r.vsc_zone) for r in rows} == {
        ("90808", 9),
        ("90045", 9),
    }


def test_load_vsc_zip_zones_reports_malformed_zone_rows() -> None:
    df = pd.DataFrame(
        {
            "Zipcode": ["30301", "60601", "1234"],
            "Dest Zone": ["abc", 99, 3],
        }
    )

    rows, invalid_count = import_air_rates.load_vsc_zip_zones(df)

    assert rows == []
    assert invalid_count == 2


def test_upsert_vsc_zones_inserts_and_updates() -> None:
    existing = _FakeVscZone(zipcode="90210", vsc_zone=8, rate_set="default")
    session = _FakeSession(existing=[existing])

    inserted, updated = import_air_rates.upsert_vsc_zones(
        session,
        [
            _FakeVscZone(zipcode="90210", vsc_zone=9, rate_set="default"),
            _FakeVscZone(zipcode="10001", vsc_zone=1, rate_set="default"),
        ],
    )

    assert inserted == 1
    assert updated == 1
    assert existing.vsc_zone == 9
    assert len(session.added) == 1
    assert session.added[0].zipcode == "10001"
