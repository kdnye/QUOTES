from __future__ import annotations

import importlib
from pathlib import Path

import config


def test_default_db_path_falls_back_when_instance_unwritable(
    monkeypatch, tmp_path
) -> None:
    """Ensure a fallback path is used when the instance directory is read-only.

    Args:
        monkeypatch: Pytest fixture for patching environment and attributes.
        tmp_path: Temporary directory provided by pytest.

    Returns:
        None. Asserts the resolved default database path uses the fallback.

    External dependencies:
        * Calls :func:`importlib.reload` to re-evaluate ``config.DEFAULT_DB_PATH``.
        * Uses :class:`pathlib.Path` to intercept instance directory creation.
    """

    fallback_dir = tmp_path / "instance"
    monkeypatch.setenv("APP_INSTANCE_DIR", str(fallback_dir))

    blocked_dir = config.BASE_DIR / "instance"
    original_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        """Raise a permission error for the default instance path."""

        if self == blocked_dir:
            raise PermissionError("read-only filesystem")
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    reloaded = importlib.reload(config)

    assert reloaded.DEFAULT_DB_PATH == fallback_dir / "app.db"
