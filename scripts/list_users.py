"""CLI utility to inspect accounts stored in the Quote Tool database.

The script uses the application's SQLAlchemy configuration (see :mod:`config`)
so it works in both local and containerized environments without additional
connection flags. It intentionally mirrors the fields exposed on
:class:`app.models.User` to help operators verify login eligibility
(``is_active`` and ``employee_approved``) and admin permissions.
"""

from __future__ import annotations

import argparse
from typing import Iterable, List, TypedDict

from sqlalchemy import Select, select
from sqlalchemy.exc import SQLAlchemyError

from app.models import User
from app.database import Session


class UserRecord(TypedDict):
    """Serializable view of a user for CLI output."""

    email: str
    role: str | None
    is_admin: bool
    employee_approved: bool
    is_active: bool


def _build_query(include_inactive: bool, admin_only: bool) -> Select[tuple[User]]:
    """Compose the SQL query for retrieving users.

    Args:
        include_inactive: When ``True`` inactive users are returned in the
            result set. The default ``False`` filters to active accounts only.
        admin_only: When ``True`` restricts results to administrative users
            (``User.is_admin``).

    Returns:
        SQLAlchemy ``Select`` statement that fetches :class:`User` rows.
    """

    query = select(User)

    if not include_inactive:
        query = query.where(User.is_active.is_(True))

    if admin_only:
        query = query.where(User.is_admin.is_(True))

    return query


def _serialize(rows: Iterable[User]) -> List[UserRecord]:
    """Convert ORM rows to dictionaries for display.

    Args:
        rows: Iterable of :class:`User` objects retrieved from the database.

    Returns:
        List of dictionaries with a stable field order suitable for CLI output.
    """

    serialized: List[UserRecord] = []
    for row in rows:
        serialized.append(
            {
                "email": row.email,
                "role": row.role,
                "is_admin": row.is_admin,
                "employee_approved": row.employee_approved,
                "is_active": row.is_active,
            }
        )
    return serialized


def list_users(
    include_inactive: bool = False, admin_only: bool = False
) -> List[UserRecord]:
    """Fetch users from the configured database.

    Args:
        include_inactive: Include inactive accounts when ``True``.
        admin_only: Restrict results to admins when ``True``.

    Returns:
        A list of :class:`UserRecord` dictionaries describing each account.

    Raises:
        SQLAlchemyError: Propagated if querying the database fails. The caller is
            responsible for error handling so the CLI can report actionable
            diagnostics.
    """

    query = _build_query(include_inactive=include_inactive, admin_only=admin_only)
    session = Session()
    try:
        results = session.scalars(query).all()
        return _serialize(results)
    finally:
        Session.remove()


def _print_records(records: List[UserRecord]) -> None:
    """Pretty-print user records to stdout."""

    if not records:
        print("No users matched the filters.")
        return

    header = "email, role, is_admin, employee_approved, is_active"
    print(header)
    for record in records:
        print(
            f"{record['email']}, {record['role']}, {record['is_admin']}, "
            f"{record['employee_approved']}, {record['is_active']}"
        )


def main() -> int:
    """Entry point for the user listing CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Return inactive users as well (defaults to active only).",
    )
    parser.add_argument(
        "--admin-only",
        action="store_true",
        help="Restrict output to admin users (is_admin = true).",
    )

    args = parser.parse_args()

    try:
        records = list_users(
            include_inactive=args.include_inactive, admin_only=args.admin_only
        )
    except SQLAlchemyError as exc:  # pragma: no cover - surfaced to operators
        print("Failed to fetch users. Confirm your database credentials and URL.")
        print(exc)
        return 1

    _print_records(records)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
