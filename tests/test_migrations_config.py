from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_alembic_ini_uses_repo_relative_script_location() -> None:
    """Ensure Alembic config points to the repo-relative migrations directory.

    Returns:
        None. Asserts the Alembic INI uses ``migrations`` for ``script_location``.
    """

    alembic_ini = PROJECT_ROOT / "alembic.ini"
    ini_contents = alembic_ini.read_text(encoding="utf-8")

    assert "script_location = migrations" in ini_contents


def test_alembic_env_sets_metadata_and_sys_path() -> None:
    """Confirm Alembic env wires metadata and a repo-root sys.path entry.

    Returns:
        None. Ensures ``Base.metadata`` is used for autogeneration and the
        repository root is inserted into ``sys.path`` for imports.
    """

    env_py = PROJECT_ROOT / "migrations" / "env.py"
    env_contents = env_py.read_text(encoding="utf-8")

    assert "target_metadata = Base.metadata" in env_contents
    assert "project_root = Path(__file__).resolve().parent.parent" in env_contents
    assert "sys.path.insert(0, str(project_root))" in env_contents
