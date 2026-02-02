from __future__ import annotations

import configparser

import config


def test_cloud_sql_dsn_preserves_socket_path_and_escapes_percent(
    monkeypatch,
) -> None:
    """Ensure Cloud SQL DSNs keep socket slashes and avoid interpolation errors.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts the socket path remains readable and ConfigParser reads
        the escaped URL without raising interpolation errors.

    External Dependencies:
        Calls :func:`config.build_cloud_sql_unix_socket_uri_from_env` to build
        the DSN and uses :class:`configparser.ConfigParser` to simulate Alembic
        configuration parsing.
    """

    monkeypatch.setenv(
        "CLOUD_SQL_CONNECTION_NAME", "quote-tool:us-central1:quotetool-db"
    )
    monkeypatch.setenv("POSTGRES_USER", "quote-tool")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss%word")
    monkeypatch.setenv("POSTGRES_DB", "quote_tool")

    dsn = config.build_cloud_sql_unix_socket_uri_from_env()

    assert dsn is not None
    assert "host=/cloudsql/" in dsn
    assert "%2F" not in dsn

    parser = configparser.ConfigParser()
    parser["alembic"] = {}
    parser.set("alembic", "sqlalchemy.url", dsn.replace("%", "%%"))

    assert "cloudsql" in parser.get("alembic", "sqlalchemy.url")
