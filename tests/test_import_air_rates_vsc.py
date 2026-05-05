from __future__ import annotations

import pandas as pd

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from scripts import import_air_rates


class _FakeZipZone:
    def __init__(self, zipcode: str, dest_zone: int, rate_set: str, beyond: str = "N"):
        self.zipcode = zipcode
        self.dest_zone = dest_zone
        self.rate_set = rate_set
        self.beyond = beyond


class _FakeZipZoneQuery:
    def __init__(self, rows: list[_FakeZipZone]):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, existing: list[_FakeZipZone] | None = None):
        self.existing = existing or []
        self.added: list[_FakeZipZone] = []

    def query(self, _model):
        return _FakeZipZoneQuery(self.existing)

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
    assert {(r.zipcode, r.dest_zone) for r in rows} == {
        ("85001", 1),
        ("00901", 2),
        ("94105", 10),
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


def test_upsert_zip_zones_updates_duplicate_zip_rows() -> None:
    existing = _FakeZipZone(
        zipcode="90210", dest_zone=2, rate_set="default", beyond="Y"
    )
    session = _FakeSession(existing=[existing])

    inserted, updated = import_air_rates.upsert_zip_zones(
        session,
        [
            _FakeZipZone(zipcode="90210", dest_zone=8, rate_set="default", beyond="N"),
            _FakeZipZone(zipcode="10001", dest_zone=1, rate_set="default", beyond="N"),
        ],
    )

    assert inserted == 1
    assert updated == 1
    assert existing.dest_zone == 8
    assert existing.beyond == "N"
    assert len(session.added) == 1
    assert session.added[0].zipcode == "10001"
