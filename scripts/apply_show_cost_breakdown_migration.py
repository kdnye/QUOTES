"""One-shot script: add show_cost_breakdown column and stamp Alembic revision.

Run from the project root when 'flask db upgrade' / 'alembic upgrade head'
cannot start because the app init fails before the migration runs.

    python scripts/apply_show_cost_breakdown_migration.py

Aborts unless the database is already at the immediate predecessor revision
(EXPECTED_DOWN_REVISION) so it cannot silently skip pending migrations.
Pass --force to bypass the check.
"""
import os
import sys

import sqlalchemy as sa

REVISION = "f7b3c9d2e1a4"
EXPECTED_DOWN_REVISION = "e5a1d2c3f4b6"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
FORCE = "--force" in sys.argv[1:]

if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL environment variable is not set.")

engine = sa.create_engine(DATABASE_URL)

with engine.begin() as conn:
    existing = conn.execute(
        sa.text("SELECT version_num FROM alembic_version")
    ).fetchall()
    current_rev = existing[0][0] if existing else None

    if current_rev == REVISION:
        print(f"Already at revision {REVISION}; ensuring column exists.")
    elif current_rev is None:
        if not FORCE:
            sys.exit(
                "ERROR: alembic_version is empty. Cannot infer DB state. "
                "Re-run with --force only if you are certain the schema "
                f"matches revision {EXPECTED_DOWN_REVISION}."
            )
        print("WARNING: alembic_version empty; --force supplied, continuing.")
    elif current_rev != EXPECTED_DOWN_REVISION:
        if not FORCE:
            sys.exit(
                f"ERROR: DB is at revision {current_rev!r}, expected "
                f"{EXPECTED_DOWN_REVISION!r}. Run 'flask db upgrade' to apply "
                "intermediate migrations first, or re-run with --force if you "
                "are certain the schema is compatible."
            )
        print(
            f"WARNING: DB at {current_rev!r} != {EXPECTED_DOWN_REVISION!r}; "
            "--force supplied, continuing."
        )

    conn.execute(sa.text(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS show_cost_breakdown BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    print("Column added (or already existed).")

    conn.execute(sa.text("DELETE FROM alembic_version"))
    conn.execute(
        sa.text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
        {"rev": REVISION},
    )
    rows = conn.execute(sa.text("SELECT version_num FROM alembic_version")).fetchall()
    print(f"Alembic version is now: {rows}")

print("Done.")
