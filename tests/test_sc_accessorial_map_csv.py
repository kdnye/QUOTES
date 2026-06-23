"""Data-file consistency guard for the SC accessorial mapping.

``app.services.science_care_quote._collect_accessorials`` emits
``SCAccessorialMap.accessorial_name`` for every checked accessorial,
which ``app.services.quote.create_quote`` then looks up against
``Accessorial.name`` via ``str.strip().lower()``. Anything that doesn't
match is silently dropped and the leg comes out priced as if the
accessorial wasn't selected at all.

This test catches the regression at the data-file level (no DB
needed): it reads ``rates/science_care/sc_accessorial_map.csv`` and
``rates/accessorial_cost.csv`` and asserts every accessorial name in
the SC map resolves to an Accessorial row under the same normalization
``create_quote`` uses.
"""

from __future__ import annotations

import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SC_MAP_CSV = PROJECT_ROOT / "rates" / "science_care" / "sc_accessorial_map.csv"
ACCESSORIAL_CSV = PROJECT_ROOT / "rates" / "accessorial_cost.csv"


def _accessorial_keys() -> set[str]:
    with ACCESSORIAL_CSV.open(newline="") as fh:
        reader = csv.DictReader(fh)
        return {
            row["Accessorial"].strip().lower()
            for row in reader
            if row.get("Accessorial") and row["Accessorial"].strip()
        }


def _sc_map_rows() -> list[dict[str, str]]:
    with SC_MAP_CSV.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_every_sc_accessorial_name_resolves_to_an_accessorial() -> None:
    accessorial_keys = _accessorial_keys()
    unmatched = [
        (row["Form Field"], row["Accessorial Name"])
        for row in _sc_map_rows()
        if row["Accessorial Name"].strip().lower() not in accessorial_keys
    ]
    assert not unmatched, (
        "sc_accessorial_map.csv references accessorial names that do not "
        "exist in accessorial_cost.csv (lookup is stripped + lowercased "
        "to match create_quote()): "
        f"{unmatched}. Selected accessorials would silently drop $0 onto "
        "the SC quote."
    )
