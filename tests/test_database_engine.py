from __future__ import annotations


def test_escape_alembic_url_escapes_percent_signs() -> None:
    """Ensure Alembic URLs escape percent signs for ConfigParser safety.

    Returns:
        None. Asserts URL-encoded socket paths are escaped as expected.
    """

    from app.database import _escape_alembic_url

    raw_url = "postgresql://user:pass@/db?host=%2Fcloudsql%2Fproject:region:inst"

    assert (
        _escape_alembic_url(raw_url)
        == "postgresql://user:pass@/db?host=%%2Fcloudsql%%2Fproject:region:inst"
    )
