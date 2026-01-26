from __future__ import annotations

from pathlib import Path

import config


def test_resolve_default_sqlite_path_uses_tmp_on_cloud_run(monkeypatch) -> None:
    """Ensure Cloud Run defaults point SQLite to ``/tmp``.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts the default SQLite path points at the fallback location.

    External Dependencies:
        Calls :func:`config._resolve_default_sqlite_path` and reads environment
        markers via :mod:`os`.
    """

    monkeypatch.setenv("K_SERVICE", "quote-tool")

    assert config._resolve_default_sqlite_path() == config.SQLITE_FALLBACK_PATH


def test_prepare_sqlite_database_uri_falls_back_on_error(monkeypatch, tmp_path) -> None:
    """Ensure SQLite falls back when the instance directory is unavailable.

    Args:
        monkeypatch: Pytest fixture for safely patching attributes.
        tmp_path: Temporary directory path provided by pytest.

    Returns:
        None. Asserts SQLite is redirected to the fallback path.

    External Dependencies:
        Calls :func:`config._prepare_sqlite_database_uri` which uses
        :func:`pathlib.Path.mkdir` to create directories.
    """

    default_path = tmp_path / "instance" / "app.db"
    fallback_path = tmp_path / "fallback" / "app.db"
    original_mkdir = Path.mkdir

    def fake_mkdir(self: Path, parents: bool = False, exist_ok: bool = False) -> None:
        """Raise an error for the default instance directory only.

        Args:
            self: Path object for the directory being created.
            parents: Whether to create parents (unused in the fake).
            exist_ok: Whether to ignore existing directories (unused in the fake).

        Returns:
            None. Raises :class:`OSError` for the instance directory.
        """

        if self == default_path.parent:
            raise OSError("read-only filesystem")
        original_mkdir(self, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(config, "SQLITE_FALLBACK_PATH", fallback_path)
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    database_uri = f"sqlite:///{default_path}"
    resolved_uri = config._prepare_sqlite_database_uri(
        database_uri,
        default_sqlite_path=default_path,
        force_instance_dir=False,
    )

    assert resolved_uri == f"sqlite:///{fallback_path}"
    assert fallback_path.parent.is_dir()
