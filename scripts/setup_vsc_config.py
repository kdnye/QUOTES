"""Seed VSC matrix and zone configuration into the database.

Run once after initial deployment (or to reset defaults):

    python scripts/setup_vsc_config.py

Three AppSetting rows are written:

* ``vsc_matrix`` — tiered diesel $/gal → FSC % schedule.  Mirrors the
  "vsc scales" sheet: price is floored to the nearest $0.25, then looked up
  in this table.  Each entry has ``min``, ``max`` (exclusive), and ``pct``
  (decimal fraction — 0.185 = 18.5 %).  Tiers run from $4.00 to $10.00 in
  $0.25 steps at 0.5 % per step; anything below $4.00 is floored to 16 %
  and anything at or above $10.00 is capped at 28 %.

* ``vsc_zones`` — maps destination zone codes (ZipZone.dest_zone) to the
  FuelSurcharge.padd_region label that sync_eia_rates.py populates:

      Zone  States                       EIA region
      ----  ---------------------------  ----------
      1     IL KY IN OH TN              PADD1   (East Coast)
      2     CT ME MA NH RI VT           PADD1A  (New England)
      3     NY NJ DE PA MD VA WV DC     PADD1B  (Central Atlantic)
      4     NC SC GA                    PADD1C  (Lower Atlantic)
      5     MI WI MN IA MO ND SD KS NE PADD2   (Midwest)
      6     TX LA OK AR FL AL MS        PADD3   (Gulf Coast)
      7     ID CO MT WY UT NM           PADD4   (Rocky Mountain)
      8     NV AZ                       PADD5   (West Coast)
      9     CA HI AK                    CA      (California)
      10    WA OR                       PADD5XCA (West Coast excl. CA)

* ``vsc_last_update`` — ISO timestamp written when this script runs.

EIA publishes updated diesel prices every Monday.  After each release run
``sync_eia_rates.py`` to refresh the FuelSurcharge table; quotes will
automatically pick up the new surcharge tier on the next request.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.models import db
from app.services.settings import set_setting

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VSC matrix — $0.25 price tiers, 0.5 % per step starting at $4.00 → 16 %
# Matches the XLOOKUP(FLOOR(price, 0.25), ...) formula in the VSC scales sheet.
# ---------------------------------------------------------------------------
VSC_MATRIX = [
    {"min": 0.00,  "max": 4.00,  "pct": 0.160},  # below $4.00 — floor at 16 %
    {"min": 4.00,  "max": 4.25,  "pct": 0.160},
    {"min": 4.25,  "max": 4.50,  "pct": 0.165},
    {"min": 4.50,  "max": 4.75,  "pct": 0.170},
    {"min": 4.75,  "max": 5.00,  "pct": 0.175},
    {"min": 5.00,  "max": 5.25,  "pct": 0.180},
    {"min": 5.25,  "max": 5.50,  "pct": 0.185},
    {"min": 5.50,  "max": 5.75,  "pct": 0.190},
    {"min": 5.75,  "max": 6.00,  "pct": 0.195},
    {"min": 6.00,  "max": 6.25,  "pct": 0.200},
    {"min": 6.25,  "max": 6.50,  "pct": 0.205},
    {"min": 6.50,  "max": 6.75,  "pct": 0.210},
    {"min": 6.75,  "max": 7.00,  "pct": 0.215},
    {"min": 7.00,  "max": 7.25,  "pct": 0.220},
    {"min": 7.25,  "max": 7.50,  "pct": 0.225},
    {"min": 7.50,  "max": 7.75,  "pct": 0.230},
    {"min": 7.75,  "max": 8.00,  "pct": 0.235},
    {"min": 8.00,  "max": 8.25,  "pct": 0.240},
    {"min": 8.25,  "max": 8.50,  "pct": 0.245},
    {"min": 8.50,  "max": 8.75,  "pct": 0.250},
    {"min": 8.75,  "max": 9.00,  "pct": 0.255},
    {"min": 9.00,  "max": 9.25,  "pct": 0.260},
    {"min": 9.25,  "max": 9.50,  "pct": 0.265},
    {"min": 9.50,  "max": 9.75,  "pct": 0.270},
    {"min": 9.75,  "max": 10.00, "pct": 0.275},
    {"min": 10.00, "max": 9999.0,"pct": 0.280},  # at or above $10.00 — cap at 28 %
]

# ---------------------------------------------------------------------------
# VSC zones — destination zone → EIA PADD region label
# Must match FuelSurcharge.padd_region values written by sync_eia_rates.py.
# ---------------------------------------------------------------------------
VSC_ZONES: dict[str, str] = {
    "1":  "PADD1",    # IL KY IN OH TN       → East Coast
    "2":  "PADD1A",   # CT ME MA NH RI VT    → New England
    "3":  "PADD1B",   # NY NJ DE PA MD VA WV DC → Central Atlantic
    "4":  "PADD1C",   # NC SC GA             → Lower Atlantic
    "5":  "PADD2",    # MI WI MN IA MO ND SD KS NE → Midwest
    "6":  "PADD3",    # TX LA OK AR FL AL MS → Gulf Coast
    "7":  "PADD4",    # ID CO MT WY UT NM    → Rocky Mountain
    "8":  "PADD5",    # NV AZ                → West Coast
    "9":  "CA",       # CA HI AK             → California
    "10": "PADD5XCA", # WA OR                → West Coast excl. CA
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
        LOGGER.info("vsc_zones saved (%d zone entries)", len(VSC_ZONES))

        now = datetime.now(timezone.utc).isoformat()
        set_setting("vsc_last_update", now)
        LOGGER.info("vsc_last_update set to %s", now)

        db.session.commit()
        LOGGER.info("VSC configuration committed successfully")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
