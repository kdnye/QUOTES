"""Guards for the Accessorial cache in ``app.services.quote``.

A previous version cached live SQLAlchemy ORM instances. After the
session that loaded them committed, those instances became detached and
their column attributes expired, so the next ``/api/quote`` call that
walked the cache (any call with at least one accessorial) blew up with
``DetachedInstanceError`` on ``str(a.name)``. The cache now stores
:class:`_AccessorialRow` snapshots; this test pins that contract so a
future refactor cannot reintroduce the bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.services import quote as quote_service
from app.services.quote import _AccessorialRow


@dataclass
class _FakeAccessorial:
    name: str
    amount: float | None
    is_percentage: bool


class _FakeQuery:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeAccessorialModel:
    """Stand-in for the ``Accessorial`` ORM class.

    Only the ``query.all()`` surface that ``_get_accessorial_rows``
    touches is implemented, so the test can run without a Flask
    application context.
    """

    def __init__(self, rows: list[Any]):
        self.query = _FakeQuery(rows)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(quote_service, "_accessorial_cache_rows", None)
    monkeypatch.setattr(quote_service, "_accessorial_cache_expires_at", 0.0)
    yield


def test_cache_holds_plain_value_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_rows = [
        _FakeAccessorial(name="4hr Window", amount=50.0, is_percentage=False),
        _FakeAccessorial(name="Weekend", amount=125.0, is_percentage=False),
        _FakeAccessorial(name="Guarantee", amount=25.0, is_percentage=True),
    ]
    monkeypatch.setattr(quote_service, "Accessorial", _FakeAccessorialModel(fake_rows))

    rows = quote_service._get_accessorial_rows()

    assert rows == [
        _AccessorialRow(name="4hr Window", amount=50.0, is_percentage=False),
        _AccessorialRow(name="Weekend", amount=125.0, is_percentage=False),
        _AccessorialRow(name="Guarantee", amount=25.0, is_percentage=True),
    ]
    # None of the entries are still ORM-attached: a None amount on the
    # source row coerces to 0.0 in the snapshot rather than propagating
    # the lazy-load hazard.
    for row in rows:
        assert isinstance(row, _AccessorialRow)


def test_none_amount_coerced_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        quote_service,
        "Accessorial",
        _FakeAccessorialModel(
            [_FakeAccessorial(name="Unset", amount=None, is_percentage=False)]
        ),
    )

    [row] = quote_service._get_accessorial_rows()

    assert row.amount == 0.0
