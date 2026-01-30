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


def test_alembic_env_escapes_percent_in_database_url() -> None:
    """Verify Alembic env escapes `%` when setting the database URL.

    Returns:
        None. Ensures the env file sets ``sqlalchemy.url`` from the app config
        while escaping percent signs for ConfigParser interpolation.

    External Dependencies:
        Reads ``migrations/env.py`` from disk via :class:`pathlib.Path`.
    """

    env_py = PROJECT_ROOT / "migrations" / "env.py"
    env_contents = env_py.read_text(encoding="utf-8")

    assert 'db_url = current_app.config.get("SQLALCHEMY_DATABASE_URI")' in env_contents
    assert 'config.set_main_option("sqlalchemy.url", str(db_url).replace("%", "%%"))' in env_contents
