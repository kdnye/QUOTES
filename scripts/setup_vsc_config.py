"""Seed default VSC matrix and zone configuration into the database.

Run once after initial deployment (or whenever you need to reset the defaults):

    python scripts/setup_vsc_config.py

The script writes two AppSetting rows:

* ``vsc_matrix`` — tiered diesel price ($/gal) → surcharge percentage table.
  Each row has ``min``, ``max`` (exclusive upper bound), and ``pct``
  (decimal fraction, e.g. ``0.35`` = 35 %).  Adjust tiers to match your
  carrier rate schedule.  EIA publishes updated diesel prices every Monday at
  https://www.eia.gov/petroleum/gasdiesel/ — run ``sync_eia_rates.py`` to
  pull the latest values into the ``FuelSurcharge`` table.

* ``vsc_zones`` — maps destination zone codes to PADD region labels so
  pricing uses the regional diesel price rather than the national average.
  Zone codes come from the ``ZipZone.dest_zone`` column.  Zones not listed
  here default to ``NATIONAL``.

* ``vsc_last_update`` — ISO timestamp recorded when this script runs.

Existing rows are overwritten on each run.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.services.settings import set_setting
from app.models import db

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tiered fuel surcharge schedule
# Tiers are matched top-to-bottom; the first tier where
#   min <= diesel_price_per_gallon < max
# is applied.  Percentages are decimal fractions (0.35 = 35 %).
#
# Based on the EIA weekly on-highway diesel price for the matched PADD region
# (or national average when the destination zone is not mapped below).
# Update these tiers to match your contracted rate schedule.
# ---------------------------------------------------------------------------
VSC_MATRIX = [
    {"min": 0.00,  "max": 2.50,   "pct": 0.05},
    {"min": 2.50,  "max": 3.00,   "pct": 0.10},
    {"min": 3.00,  "max": 3.50,   "pct": 0.15},
    {"min": 3.50,  "max": 4.00,   "pct": 0.20},
    {"min": 4.00,  "max": 4.50,   "pct": 0.25},
    {"min": 4.50,  "max": 5.00,   "pct": 0.30},
    {"min": 5.00,  "max": 5.50,   "pct": 0.35},
    {"min": 5.50,  "max": 6.00,   "pct": 0.40},
    {"min": 6.00,  "max": 6.50,   "pct": 0.45},
    {"min": 6.50,  "max": 9999.0, "pct": 0.50},
]

# ---------------------------------------------------------------------------
# Destination zone → PADD region mapping
# Keys are dest_zone values from the ZipZone table (typically "1"–"8" or
# "NATIONAL").  Values must match FuelSurcharge.padd_region rows populated
# by sync_eia_rates.py (NATIONAL, PADD1, PADD2, PADD3, PADD4).
#
# Zones not listed here fall back to NATIONAL automatically.
# Customize this mapping to match your service territory.
# ---------------------------------------------------------------------------
VSC_ZONES: dict[str, str] = {
    # Example mapping — adjust zone codes to match your ZipZone data.
    # "1": "PADD1",   # Northeast / East Coast
    # "2": "PADD1",   # Mid-Atlantic
    # "3": "PADD2",   # Midwest
    # "4": "PADD2",   # Central
    # "5": "PADD3",   # South / Gulf Coast
    # "6": "PADD3",   # Southeast
    # "7": "PADD4",   # Mountain West
    # "8": "PADD4",   # Northern Plains
    "NATIONAL": "NATIONAL",
}


def main() -> int:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = create_app()
    with app.app_context():
        set_setting("vsc_matrix", json.dumps(VSC_MATRIX))
        LOGGER.info("vsc_matrix saved (%d tiers)", len(VSC_MATRIX))

        set_setting("vsc_zones", json.dumps(VSC_ZONES))
        LOGGER.info("vsc_zones saved (%d entries)", len(VSC_ZONES))

        now = datetime.now(timezone.utc).isoformat()
        set_setting("vsc_last_update", now)
        LOGGER.info("vsc_last_update set to %s", now)

        db.session.commit()
        LOGGER.info("VSC configuration committed successfully")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
