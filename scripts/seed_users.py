"""Command-line utility for seeding :class:`app.models.User` accounts.

The script reads a comma-separated value (CSV) file describing user
accounts, validates the data, and inserts the rows into the application's
database. It can operate as a standalone tool by constructing a Flask app via
``app.create_app`` or it can receive a pre-configured application instance
from tests. The default CSV path points at ``users_seed_template.csv`` in the
repository root which contains two example rows ready to customize.

Example::

    $ python scripts/seed_users.py --file users_seed_template.csv

Use ``--update-existing`` to refresh existing accounts and ``--dry-run`` to
preview the changes without committing them.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from flask import Flask

# Ensure imports work when the script is executed directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app import create_app
from app.models import User, db
from app.services.auth_utils import is_valid_email, is_valid_password, is_valid_phone

TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}
ALLOWED_ROLES = {"customer", "employee", "super_admin"}
DEFAULT_CSV = ROOT / "users_seed_template.csv"


@dataclass
class SeedResult:
    """Summary of the seeding operation.

    Attributes:
        created: Number of new :class:`app.models.User` records inserted.
        updated: Number of existing users that were modified.
        skipped: Rows ignored because ``update_existing`` was ``False`` or
            validation failed.
        errors: Human readable validation messages keyed by CSV row number.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedRow:
    """Normalized representation of a CSV row ready for insertion.

    Attributes:
        email: Lower-cased email address used to look up existing users.
        attributes: Mapping of :class:`app.models.User` column names to values
            (for example ``{"first_name": "Ada"}``).
        password: Plain-text password that will be hashed via
            :meth:`app.models.User.set_password`. ``None`` indicates that the
            row supplied a pre-hashed password.
        password_hash: Pre-hashed password to assign directly to
            ``User.password_hash`` when ``password`` is ``None``.
    """

    email: str
    attributes: dict[str, Any]
    password: str | None
    password_hash: str | None


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    """Return ``True`` or ``False`` for CSV cell values representing booleans.

    Args:
        value: Raw value from :class:`csv.DictReader` which may be ``None``,
            a string, or numeric type.
        default: Fallback value when ``value`` is empty or ambiguous.

    Returns:
        ``True`` or ``False`` depending on the provided value. Strings are
        normalized to lower-case and compared against ``TRUE_VALUES`` and
        ``FALSE_VALUES``. Non-string truthy values fall back to ``bool(value)``.
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = value.strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return default


def _looks_like_password_hash(value: str) -> bool:
    """Return ``True`` when ``value`` resembles Werkzeug's hash format."""

    if value.count("$") < 2:
        return False
    prefix = value.split("$", 1)[0]
    return ":" in prefix


def _read_csv_rows(csv_path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield row dictionaries from ``csv_path`` skipping comments and blanks.

    Args:
        csv_path: Location of the CSV file.

    Yields:
        Tuples containing the 1-based row number and the normalized row
        dictionary produced by :class:`csv.DictReader`.
    """

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        filtered_lines = (
            line
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        )
        reader = csv.DictReader(filtered_lines)
        for index, row in enumerate(reader, start=2):
            normalized = {
                (key or "").strip(): (
                    value.strip() if isinstance(value, str) else value
                )
                for key, value in row.items()
            }
            yield index, normalized


def _normalize_row(row: dict[str, str]) -> tuple[NormalizedRow | None, list[str]]:
    """Validate and coerce raw CSV data into :class:`NormalizedRow` objects.

    Args:
        row: Mapping produced by :func:`_read_csv_rows`.

    Returns:
        Tuple where the first element is a populated :class:`NormalizedRow`
        instance (``None`` when validation fails) and the second element is a
        list of error strings describing the validation issues.
    """

    errors: list[str] = []

    email = (row.get("email") or "").strip().lower()
    if not email:
        errors.append("Email is required.")
    elif not is_valid_email(email):
        errors.append("Email is not syntactically valid.")

    raw_password = row.get("password") or ""
    raw_password = raw_password.strip()
    password_hash: str | None = None
    password: str | None
    if not raw_password:
        errors.append("Password is required.")
        password = None
    elif _looks_like_password_hash(raw_password):
        password_hash = raw_password
        password = None
    else:
        password = raw_password
        if not is_valid_password(password):
            errors.append(
                "Password must meet complexity requirements or provide a pre-hashed value."
            )

    name = (row.get("name") or "").strip() or None
    first_name = (row.get("first_name") or "").strip() or None
    last_name = (row.get("last_name") or "").strip() or None
    if name is None:
        combined = " ".join(part for part in [first_name, last_name] if part)
        name = combined or None

    phone = (row.get("phone") or "").strip() or None
    if phone and not is_valid_phone(phone):
        errors.append("Phone number is not in a dialable format.")

    company_name = (row.get("company_name") or "").strip() or None
    company_phone = (row.get("company_phone") or "").strip() or None
    if company_phone and not is_valid_phone(company_phone):
        errors.append("Company phone number is not in a dialable format.")

    raw_role = (row.get("role") or "customer").strip().lower()
    role = raw_role or "customer"
    if role not in ALLOWED_ROLES:
        errors.append(f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}.")

    is_admin = _parse_bool(row.get("is_admin"), default=False)
    employee_approved = _parse_bool(row.get("employee_approved"), default=False)
    is_active = _parse_bool(row.get("is_active"), default=True)

    # Keep role and admin flags synchronized with the admin dashboard rules.
    if role == "super_admin":
        is_admin = True
        employee_approved = True
    elif is_admin:
        role = "super_admin"
        employee_approved = True
    elif role != "employee":
        employee_approved = False

    attributes = {
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "company_name": company_name,
        "company_phone": company_phone,
        "role": role,
        "employee_approved": employee_approved,
        "is_admin": is_admin,
        "is_active": is_active,
    }

    if errors:
        return None, errors

    return (
        NormalizedRow(
            email=email,
            attributes=attributes,
            password=password,
            password_hash=password_hash,
        ),
        [],
    )


def _apply_row(
    normalized: NormalizedRow,
    *,
    update_existing: bool,
) -> tuple[str, bool]:
    """Insert or update a user using normalized row data.

    Args:
        normalized: Pre-validated row produced by :func:`_normalize_row`.
        update_existing: When ``True`` existing users are updated instead of
            being skipped.

    Returns:
        Tuple containing the email address and a boolean indicating whether a
        new user was created (``True``) or an existing record was updated
        (``False``).
    """

    user = User.query.filter_by(email=normalized.email).first()
    created = False
    if user is None:
        user = User(email=normalized.email)
        created = True
    elif not update_existing:
        return normalized.email, created

    for attr, value in normalized.attributes.items():
        setattr(user, attr, value)

    if normalized.password_hash:
        user.password_hash = normalized.password_hash
    elif normalized.password:
        user.set_password(normalized.password)

    db.session.add(user)
    return normalized.email, created


def seed_users_from_csv(
    csv_path: Path,
    *,
    update_existing: bool = False,
    dry_run: bool = False,
    app: Flask | None = None,
) -> SeedResult:
    """Seed users defined in ``csv_path`` into the configured database.

    Args:
        csv_path: Location of the CSV file to process.
        update_existing: When ``True`` rows matching existing email addresses
            update those accounts instead of being skipped.
        dry_run: When ``True`` the database is left unchanged. Useful for
            validating the CSV without modifying production data.
        app: Optional Flask application. When omitted the function loads
            environment variables via :func:`dotenv.load_dotenv` and creates an
            application using :func:`app.create_app`.

    Returns:
        :class:`SeedResult` describing how many users were created, updated, or
        skipped. Validation errors are included in ``SeedResult.errors``.
    """

    if app is None:
        load_dotenv()
        flask_app = create_app()
    else:
        flask_app = app

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    result = SeedResult()

    with flask_app.app_context():
        db.create_all()

        normalized_rows: list[tuple[int, NormalizedRow]] = []
        for row_number, row in _read_csv_rows(csv_path):
            normalized, errors = _normalize_row(row)
            if errors:
                result.skipped += 1
                joined = "; ".join(errors)
                result.errors.append(f"Row {row_number}: {joined}")
                continue
            normalized_rows.append((row_number, normalized))

        for row_number, normalized in normalized_rows:
            email, created = _apply_row(normalized, update_existing=update_existing)
            if created:
                result.created += 1
            elif email:
                if update_existing:
                    result.updated += 1
                else:
                    result.skipped += 1

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        db.session.remove()

    return result


def main() -> None:
    """Entry point for command-line execution.

    Parses CLI arguments, calls :func:`seed_users_from_csv`, and prints a
    human-friendly summary describing how many users were created, updated, or
    skipped.
    """

    from argparse import ArgumentParser

    parser = ArgumentParser(description="Seed users from a CSV file.")
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_CSV,
        help="Path to the CSV file containing user records.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update accounts when the email already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV without committing changes.",
    )
    args = parser.parse_args()

    result = seed_users_from_csv(
        args.file,
        update_existing=args.update_existing,
        dry_run=args.dry_run,
    )

    print(
        "✅ Processed CSV: created={created}, updated={updated}, skipped={skipped}".format(
            created=result.created,
            updated=result.updated,
            skipped=result.skipped,
        )
    )
    if result.errors:
        print("⚠️ Encountered validation issues:")
        for message in result.errors:
            print(f"  - {message}")


if __name__ == "__main__":
    main()
