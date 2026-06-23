"""Guards for the Accessorial cache in ``app.services.quote``.

A previous version cached live SQLAlchemy ORM instances. After the
session that loaded them committed, those instances became detached and
their column attributes expired, so the next ``/api/quote`` call that
walked the cache (any call with at least one accessorial) blew up with
``DetachedInstanceError`` on ``str(a.name)``. The cache now stores
:class:`_AccessorialRow` snapshots; this test pins that contract so a
future refactor cannot reintroduce the bug.

It also pins ``_session_scope``'s app-context routing: under a Flask
app context the loader must read through Flask-SQLAlchemy's
``db.session`` (so a test that swaps ``SQLALCHEMY_DATABASE_URI`` is
honored), and only fall back to the standalone
:class:`app.database.Session` when no context is available.
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


class _FakeSession:
    """Standalone ``Session()`` stub — context manager + ``query().all()``."""

    def __init__(self, rows: list[Any]):
        self._rows = rows

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def query(self, _model: Any) -> _FakeQuery:
        return _FakeQuery(self._rows)


class _FakeFlaskDb:
    """Stub for ``app.models.db`` — only ``.session.query().all()`` is used."""

    def __init__(self, rows: list[Any]):
        self.session = _FakeSession(rows)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(quote_service, "_accessorial_cache_rows", None)
    monkeypatch.setattr(quote_service, "_accessorial_cache_expires_at", 0.0)
    yield


def _patch_standalone(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> None:
    monkeypatch.setattr(quote_service, "has_app_context", lambda: False)
    monkeypatch.setattr(quote_service, "Session", lambda: _FakeSession(rows))


def _patch_app_context(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> None:
    monkeypatch.setattr(quote_service, "has_app_context", lambda: True)
    monkeypatch.setattr(quote_service, "db", _FakeFlaskDb(rows))


def test_cache_holds_plain_value_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_standalone(
        monkeypatch,
        [
            _FakeAccessorial(name="4hr Window", amount=50.0, is_percentage=False),
            _FakeAccessorial(name="Weekend", amount=125.0, is_percentage=False),
            _FakeAccessorial(name="Guarantee", amount=25.0, is_percentage=True),
        ],
    )

    rows = quote_service._get_accessorial_rows()

    assert rows == [
        _AccessorialRow(name="4hr Window", amount=50.0, is_percentage=False),
        _AccessorialRow(name="Weekend", amount=125.0, is_percentage=False),
        _AccessorialRow(name="Guarantee", amount=25.0, is_percentage=True),
    ]
    for row in rows:
        assert isinstance(row, _AccessorialRow)


def test_none_amount_coerced_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_standalone(
        monkeypatch,
        [_FakeAccessorial(name="Unset", amount=None, is_percentage=False)],
    )

    [row] = quote_service._get_accessorial_rows()

    assert row.amount == 0.0


def test_app_context_reads_through_db_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under a Flask app context the loader must use ``db.session``.

    The standalone ``Session()`` is intentionally not called here -
    monkeypatching it to raise gives us a hard signal if the loader
    ever ignores the app context.
    """

    monkeypatch.setattr(
        quote_service,
        "Session",
        lambda: pytest.fail("Session() must not be opened under an app context"),
    )
    _patch_app_context(
        monkeypatch,
        [_FakeAccessorial(name="4hr Window", amount=50.0, is_percentage=False)],
    )

    [row] = quote_service._get_accessorial_rows()

    assert row == _AccessorialRow(
        name="4hr Window", amount=50.0, is_percentage=False
    )
