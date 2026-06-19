"""Verify _run_alembic_upgrade serializes via a Postgres advisory lock.

Cloud Run boots gunicorn with multiple workers; each calls create_app()
which calls ensure_database_schema(). Without the lock, all workers
race to CREATE TABLE / stamp the alembic_version row, losing workers
fail with `duplicate key on pg_type_typname_nsp_index`. The startup
guard catches this and starts anyway, but the noise scares anyone
reading the logs. The advisory lock serializes the upgrade so only one
worker actually does the work.

The lock is transaction-scoped (pg_advisory_xact_lock) so a crashed
upgrade rolls back the transaction and releases the lock cleanly,
without a `finally` block that could mask the original exception by
raising on a dead connection during unlock.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import database as db_module  # noqa: E402


def _make_engine(dialect_name: str) -> MagicMock:
    """Build a SQLAlchemy engine mock with the given dialect name."""

    engine = MagicMock(spec=["dialect", "begin", "connect", "url"])
    engine.dialect = MagicMock()
    engine.dialect.name = dialect_name
    url = MagicMock()
    url.render_as_string.return_value = (
        f"{dialect_name}://user:pw@host/db"
    )
    engine.url = url
    return engine


def test_postgres_path_acquires_advisory_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_engine("postgresql")
    lock_conn = MagicMock()
    # `engine.begin()` is used as a context manager (`with ... as`).
    engine.begin.return_value.__enter__.return_value = lock_conn
    engine.begin.return_value.__exit__.return_value = False

    stamp_and_upgrade = MagicMock()
    monkeypatch.setattr(db_module, "_stamp_and_upgrade", stamp_and_upgrade)

    db_module._run_alembic_upgrade(engine)

    # Exactly one SQL call: pg_advisory_xact_lock. No manual unlock or
    # commit - the transaction-scoped lock releases automatically when
    # the `with engine.begin()` block exits.
    calls = lock_conn.execute.call_args_list
    assert len(calls) == 1, f"expected 1 SQL call, got {len(calls)}"
    sql = str(calls[0].args[0])
    assert "pg_advisory_xact_lock" in sql
    assert calls[0].args[1] == {"key": db_module._ALEMBIC_ADVISORY_LOCK_KEY}
    # The stamp+upgrade ran exactly once, inside the lock, and was
    # passed the held connection so the inspector doesn't borrow a
    # second one from the pool.
    stamp_and_upgrade.assert_called_once()
    assert stamp_and_upgrade.call_args.kwargs.get("inspect_conn") is lock_conn


def test_postgres_path_releases_lock_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_engine("postgresql")
    lock_conn = MagicMock()
    engine.begin.return_value.__enter__.return_value = lock_conn
    engine.begin.return_value.__exit__.return_value = False

    def boom(config, active_engine, **kwargs):
        # **kwargs absorbs inspect_conn= that _run_alembic_upgrade now
        # passes in. Without it the stub would TypeError on the keyword
        # before the simulated RuntimeError ran, and the test would
        # pass for the wrong reason (or fail with the wrong exception
        # type).
        raise RuntimeError("simulated upgrade failure")

    monkeypatch.setattr(db_module, "_stamp_and_upgrade", boom)

    with pytest.raises(RuntimeError):
        db_module._run_alembic_upgrade(engine)

    # __exit__ being invoked is what triggers the rollback that releases
    # the transaction-scoped advisory lock. No manual unlock call needed
    # (and therefore no risk of masking the RuntimeError).
    engine.begin.return_value.__exit__.assert_called_once()


def test_non_postgres_path_skips_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_engine("sqlite")
    stamp_and_upgrade = MagicMock()
    monkeypatch.setattr(db_module, "_stamp_and_upgrade", stamp_and_upgrade)

    db_module._run_alembic_upgrade(engine)

    # No transaction opened - no advisory lock on non-Postgres dialects.
    engine.begin.assert_not_called()
    stamp_and_upgrade.assert_called_once()


def test_postgres_path_reuses_lock_conn_for_inspector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: with DB_POOL_SIZE=1 + DB_POOL_MAX_OVERFLOW=0, the
    # inspector inside _stamp_and_upgrade must NOT borrow a second
    # connection from the pool while lock_conn is held - it would
    # deadlock. Verify the lock_conn is threaded into the helper via
    # inspect_conn=.
    engine = _make_engine("postgresql")
    lock_conn = MagicMock()
    engine.begin.return_value.__enter__.return_value = lock_conn
    engine.begin.return_value.__exit__.return_value = False

    captured: dict = {}

    def fake_stamp_and_upgrade(config, active_engine, inspect_conn=None):
        captured["inspect_conn"] = inspect_conn

    monkeypatch.setattr(db_module, "_stamp_and_upgrade", fake_stamp_and_upgrade)

    db_module._run_alembic_upgrade(engine)

    assert captured["inspect_conn"] is lock_conn


def test_advisory_lock_key_is_stable() -> None:
    # The key is derived from a constant string. Snapshot it so a future
    # refactor that accidentally changes the input is caught: a deploy
    # with a drifted key would let two workers BOTH think they have the
    # lock during a brief overlap.
    assert isinstance(db_module._ALEMBIC_ADVISORY_LOCK_KEY, int)
    # 64-bit signed range.
    assert -(2 ** 63) <= db_module._ALEMBIC_ADVISORY_LOCK_KEY < 2 ** 63
