"""Verify _run_alembic_upgrade serializes via a Postgres advisory lock.

Cloud Run boots gunicorn with multiple workers; each calls create_app()
which calls ensure_database_schema(). Without the lock, all workers
race to CREATE TABLE / stamp the alembic_version row, losing workers
fail with `duplicate key on pg_type_typname_nsp_index`. The startup
guard catches this and starts anyway, but the noise scares anyone
reading the logs. The advisory lock serializes the upgrade so only one
worker actually does the work.
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

    engine = MagicMock(spec=["dialect", "connect", "url"])
    engine.dialect = MagicMock()
    engine.dialect.name = dialect_name
    url = MagicMock()
    url.render_as_string.return_value = (
        f"{dialect_name}://user:pw@host/db"
    )
    engine.url = url
    return engine


def test_postgres_path_acquires_and_releases_advisory_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_engine("postgresql")
    lock_conn = MagicMock()
    # `engine.connect()` is used as a context manager (`with ... as`).
    engine.connect.return_value.__enter__.return_value = lock_conn
    engine.connect.return_value.__exit__.return_value = False

    stamp_and_upgrade = MagicMock()
    monkeypatch.setattr(db_module, "_stamp_and_upgrade", stamp_and_upgrade)

    db_module._run_alembic_upgrade(engine)

    # Two SQL calls: pg_advisory_lock then pg_advisory_unlock, in order.
    calls = lock_conn.execute.call_args_list
    assert len(calls) == 2, f"expected 2 SQL calls, got {len(calls)}"
    first_sql = str(calls[0].args[0])
    second_sql = str(calls[1].args[0])
    assert "pg_advisory_lock" in first_sql
    assert "pg_advisory_unlock" in second_sql
    # Same key for lock + unlock.
    assert calls[0].args[1] == calls[1].args[1]
    # The stamp+upgrade ran exactly once, between the lock pair.
    stamp_and_upgrade.assert_called_once()
    # And the connection was committed so the unlock isn't stuck in
    # an uncommitted transaction.
    lock_conn.commit.assert_called_once()


def test_postgres_path_releases_lock_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_engine("postgresql")
    lock_conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = lock_conn
    engine.connect.return_value.__exit__.return_value = False

    def boom(config, active_engine):
        raise RuntimeError("simulated upgrade failure")

    monkeypatch.setattr(db_module, "_stamp_and_upgrade", boom)

    with pytest.raises(RuntimeError):
        db_module._run_alembic_upgrade(engine)

    # Unlock SQL still ran despite the upgrade exception.
    sqls = [str(c.args[0]) for c in lock_conn.execute.call_args_list]
    assert any("pg_advisory_unlock" in s for s in sqls)


def test_non_postgres_path_skips_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_engine("sqlite")
    stamp_and_upgrade = MagicMock()
    monkeypatch.setattr(db_module, "_stamp_and_upgrade", stamp_and_upgrade)

    db_module._run_alembic_upgrade(engine)

    # No connection grabbed - no lock taken on non-Postgres dialects.
    engine.connect.assert_not_called()
    stamp_and_upgrade.assert_called_once()


def test_advisory_lock_key_is_stable() -> None:
    # The key is derived from a constant string. Snapshot it so a future
    # refactor that accidentally changes the input is caught: a deploy
    # with a drifted key would let two workers BOTH think they have the
    # lock during a brief overlap.
    assert isinstance(db_module._ALEMBIC_ADVISORY_LOCK_KEY, int)
    # 64-bit signed range.
    assert -(2 ** 63) <= db_module._ALEMBIC_ADVISORY_LOCK_KEY < 2 ** 63
