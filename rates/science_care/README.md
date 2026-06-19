# Science Care reference data seed

Six CSVs that populate the SC reference tables backing the
`/sc/quote` multi-lab quote page. Generated from the customer's
`SC_MASTER_TOOL_2026__060626__FSI_API_TEST.xlsm`, normalised to the
column headers declared in `app/science_care/csv_admin.py:SC_TABLE_SPECS`
so the existing upload UI (`/sc/reference/<table>/upload`) accepts them
as-is.

## Upload procedure (first deploy)

Sign in as an SC admin (`User.is_sc_admin = True` with
`rate_set = "science_care"`, or an FSI super-admin). The upload form
is at `https://<host>/sc/reference/<table>/upload`. For the first
seed of a fresh deployment, use **Replace existing data** so the
operation is idempotent.

Upload in this order — later tables reference earlier ones:

| # | Table | URL slug | Rows | Action |
|---|---|---|---|---|
| 1 | SC Labs | `sc_labs` | 7 | Replace |
| 2 | SC Box Types | `sc_box_types` | 4 | Replace |
| 3 | SC Tissue Codes | `sc_tissue_codes` | 179 | Replace |
| 4 | SC Consumables | `sc_consumables` | 5 | Replace |
| 5 | SC Established Lanes | `sc_established_lanes` | 24 | Replace |
| 6 | SC Accessorial Map | `sc_accessorial_map` | 5 | Replace |

Subsequent edits (lab phone numbers, new tissues, etc.) use the same
UI in **Add new rows** mode unless you want a full reseed.

## Verification

After the six uploads:

```python
flask shell
>>> from app.models import (
...     SCAccessorialMap, SCBoxType, SCConsumable,
...     SCEstablishedLane, SCLab, SCTissueCode,
...     RATE_SET_SCIENCE_CARE,
... )
>>> for M in (SCLab, SCBoxType, SCTissueCode, SCConsumable,
...           SCEstablishedLane, SCAccessorialMap):
...     n = M.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE).count()
...     print(f"{M.__name__:20}  {n}")
SCLab                 7
SCBoxType             4
SCTissueCode        179
SCConsumable          5
SCEstablishedLane    24
SCAccessorialMap      5
```

Then run an end-to-end smoke: `/sc/quote` → pick `SCPA` on leg 1,
destination `07430` (Mahwah), tissue `ARM01` qty 2, accessorial `J8`
(Liftgate). Generate quotes. Expected: Air + Hotshot from the FSI
API, Established Lane = $825, winner is the cheapest of the three.

## Mapping decisions

These are the three notable simplifications applied while flattening
the workbook into the existing schema. Each carries a small loss of
fidelity that's deliberately accepted; flag any of them if the
customer reports anomalies.

### 1. Tissue → box-type capacity collapse

The workbook gives a per-tissue capacity **matrix** — four columns
(Medium / Large / X-Large / Airtray) per tissue, each holding the
pieces-per-box for that box type. The `SCTissueCode` schema has
**one** `default_box_type_code` + **one** `pieces_per_box`. The seed
picks the **highest-capacity non-zero column** for each row with
priority `XLG > LRG > MED > AIRTRAY`. Distribution in the generated
CSV: 153× XLG, 18× AIRTRAY, 4× LRG, 2× MED.

Two equipment rentals (`MOBILE KITS`, `TABLE01`) carry no box
allocation because the workbook records zeros across all four box
columns for them — they ship as separate logistics. Their Notes
column flags this so the SC admin doesn't think it's a mistake.

A future PR could add a `sc_tissue_box_capacity(tissue_code,
box_code, pieces)` join table if the customer needs full-matrix
allocation fidelity.

### 2. SCPA ZIP discrepancy

The workbook's "Drop downs OTH - SC" sheet says SCPA is at ZIP
`19032`, while "Data Validation" says `19153`. The lab dropdown on
every SHIPMENT tab reads from "Drop downs OTH - SC", so `sc_labs.csv`
uses `19032`. If the customer says the correct ZIP is `19153`, edit
that single cell and re-upload `sc_labs.csv`.

### 3. Established-lane city → ZIP hand-map

The workbook's "Established Lanes" sheet stores destinations as
`City,State` strings (e.g. `Las Vegas,NV`) or as SC lab codes
(e.g. `SCCO` for SC-to-SC). The `SCEstablishedLane` schema has a
`dest_zip` column. The seed maps:

- **SC-to-SC** destinations → the destination lab's `origin_zip`.
- **City destinations** → a representative ZIP per metro, hand-picked
  by the seed script (see the script in this commit for the table).

This means an established lane only matches when the leg's
destination ZIP **exactly equals** the mapped representative ZIP
(e.g. for "Mahwah,NJ" only ZIP `07430` will trigger the $825 lane).
A leg to ZIP `07431` (which also serves Mahwah) won't pick up the
established rate. If this becomes a real-world problem the schema
could grow a list-of-zips per lane; for now, accept the simplification.

## Source workbook lineage

| CSV | Source sheet / rows |
|---|---|
| `sc_labs.csv` | `Drop downs OTH - SC` rows 2–8 (lab code → origin ZIP) |
| `sc_box_types.csv` | `SHIPMENT 1` rows 26–29 (Medium / Large / X-Large / Airtray dimensions + tare) |
| `sc_tissue_codes.csv` | `Items Weights Boxes - SC` rows 2–180 (tissue code + description + avg weight + per-box capacity matrix → collapsed) |
| `sc_consumables.csv` | `SHIPMENT 1` rows 32–36 (RTU / FRZN gel-pack & dry-ice weights per box, domestic & intl) |
| `sc_established_lanes.csv` | `Established Lanes` rows 4–27 (origin lab + destination → rate; destinations resolved to ZIPs as above) |
| `sc_accessorial_map.csv` | `SHIPMENT 1` rows 3–8 column D (workbook labels) cross-referenced with `_FALLBACK_ACCESSORIAL_LABELS` in `app/science_care/routes.py:64` |

All CSVs are stamped with `rate_set = "science_care"` automatically by
the upload route's `force_science_care_rate_set()`. The CSVs do not
include a `rate_set` column.
