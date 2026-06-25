"""create sc_international_lanes table and seed from FSI VSC-Locked workbook

Revision ID: d8a4f9c1b2e6
Revises: c5d7f1e9a2b3
Create Date: 2026-06-25 17:30:00.000000

Adds the ``sc_international_lanes`` table that mirrors the
``International Quotes`` tab of the FSI Shipping Quote Tool 2026 VSC-Locked
workbook (1,099 lanes, 7 origin labs * ~157 destinations + airport-only
rows). Rows are keyed by (destination display string, lab_code) and store
``min_charge`` / ``per_lb`` / ``weight_break`` plus an optional
``cost_per_km_over_80`` for door-to-door surcharges.

Quote math is documented on :class:`app.models.SCInternationalLane`:
    IF(weight > weight_break,
       ((weight - weight_break) * per_lb) + min_charge,
       min_charge) + intl_hotshot_km_surcharge

No VSC, no accessorials, no fuel surcharge.

Seeding pulls from ``rates/international_lanes.csv`` (extracted verbatim
from ``International Quotes!B4:O1102``). The CSV ships in the repo so
fresh deploys get the same 1,099 lanes.
"""

import csv
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d8a4f9c1b2e6"
down_revision: Union[str, Sequence[str], None] = "c5d7f1e9a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_CSV = Path(__file__).resolve().parents[2] / "rates" / "international_lanes.csv"


def _to_float(value, *, allow_blank: bool = True) -> "float | None":
    text = str(value or "").strip()
    if not text:
        return None if allow_blank else 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return None if allow_blank else 0.0


def upgrade() -> None:
    op.create_table(
        "sc_international_lanes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("destination", sa.String(120), nullable=False, index=True),
        sa.Column("country", sa.String(80), nullable=False),
        sa.Column("notes", sa.String(40), nullable=True),
        sa.Column(
            "rate_class",
            sa.String(40),
            nullable=False,
            server_default="Standard",
        ),
        sa.Column("lab_code", sa.String(8), nullable=False, index=True),
        sa.Column("airport_code_1", sa.String(8), nullable=True),
        sa.Column("airport_code_2", sa.String(8), nullable=True),
        sa.Column("airport_code_3", sa.String(8), nullable=True),
        sa.Column("min_charge", sa.Float, nullable=False),
        sa.Column("per_lb", sa.Float, nullable=False),
        sa.Column("weight_break", sa.Float, nullable=False),
        sa.Column("cost_per_km_over_80", sa.Float, nullable=True),
        sa.Column("special_notes", sa.Text, nullable=True),
        sa.Column(
            "rate_set",
            sa.String(50),
            nullable=False,
            server_default="science_care",
            index=True,
        ),
        sa.UniqueConstraint(
            "rate_set",
            "destination",
            "lab_code",
            name="uq_sc_intl_lanes_rate_set_dest_lab",
        ),
    )

    if not SEED_CSV.exists():
        # Without the seed CSV the table is created empty; the importer can
        # be rerun later via scripts/import_air_rates.py or an admin bulk
        # upload. Don't fail the migration if the file is absent.
        return

    rows_to_insert = []
    with SEED_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            destination = (row.get("Selectable Destination") or "").strip()
            lab_code = (row.get("Lab") or "").strip()
            if not destination or not lab_code:
                continue
            min_charge = _to_float(row.get("Min"), allow_blank=False) or 0.0
            per_lb = _to_float(row.get("Per Lb"), allow_blank=False) or 0.0
            weight_break = _to_float(row.get("Weight Break"))
            if weight_break is None and per_lb:
                # Mirror the workbook formula M = K/L when the CSV cell
                # is blank (older exports left it computed in Excel only).
                weight_break = min_charge / per_lb
            elif weight_break is None:
                weight_break = 0.0
            rows_to_insert.append(
                {
                    "destination": destination,
                    "country": (row.get("Country") or "").strip(),
                    "notes": (row.get("Notes") or "").strip() or None,
                    "rate_class": (row.get("Standard or Customer Specific") or "Standard").strip(),
                    "lab_code": lab_code,
                    "airport_code_1": (row.get("Airport Code 1") or "").strip() or None,
                    "airport_code_2": (row.get("Airport Code 2") or "").strip() or None,
                    "airport_code_3": (row.get("Airport Code 3") or "").strip() or None,
                    "min_charge": min_charge,
                    "per_lb": per_lb,
                    "weight_break": weight_break,
                    "cost_per_km_over_80": _to_float(row.get("Cost per km Over 80km")),
                    "special_notes": (row.get("Special Notes") or "").strip() or None,
                    "rate_set": "science_care",
                }
            )

    if rows_to_insert:
        op.bulk_insert(
            sa.table(
                "sc_international_lanes",
                sa.column("destination", sa.String),
                sa.column("country", sa.String),
                sa.column("notes", sa.String),
                sa.column("rate_class", sa.String),
                sa.column("lab_code", sa.String),
                sa.column("airport_code_1", sa.String),
                sa.column("airport_code_2", sa.String),
                sa.column("airport_code_3", sa.String),
                sa.column("min_charge", sa.Float),
                sa.column("per_lb", sa.Float),
                sa.column("weight_break", sa.Float),
                sa.column("cost_per_km_over_80", sa.Float),
                sa.column("special_notes", sa.Text),
                sa.column("rate_set", sa.String),
            ),
            rows_to_insert,
        )


def downgrade() -> None:
    op.drop_table("sc_international_lanes")
