"""One-shot script: add show_cost_breakdown column and stamp Alembic revision.

Run from the project root when 'flask db upgrade' / 'alembic upgrade head'
cannot start because the app init fails before the migration runs.

    python scripts/apply_show_cost_breakdown_migration.py
"""
import os
import sys

import sqlalchemy as sa

REVISION = "f7b3c9d2e1a4"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL environment variable is not set.")

engine = sa.create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(sa.text(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS show_cost_breakdown BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    print("Column added (or already existed).")

    conn.execute(sa.text(
        "UPDATE alembic_version SET version_num = :rev"
    ), {"rev": REVISION})
    rows = conn.execute(sa.text("SELECT version_num FROM alembic_version")).fetchall()
    print(f"Alembic version is now: {rows}")

print("Done.")
