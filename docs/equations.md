# Equations & Business Logic

Authoritative human-readable record of every formula, threshold, and
calculation used by the Quotes app. The code is the source of execution, but
this document is the source of truth for what the code *should* be doing.

When a formula changes in code, update the corresponding entry here in the same
commit (per `CLAUDE.md` Section 2). Removed equations are kept as audit history
with a `[REMOVED YYYY-MM-DD]` marker — IDs are never reused.

## Summary

| ID | Name | Implementation |
| --- | --- | --- |
| EQ-001 | Dimensional weight | `app/services/constants.py` (`DIM_DIVISOR`); applied in `app/services/quote.py` and `app/services/science_care_quote.py` |
| EQ-002 | Science Care per-tissue box allocation | `app/services/science_care_quote.py`, `recommended_box_for_qty()` and `allocate_boxes()` |
| EQ-003 | Science Care leg total weight | `app/services/science_care_quote.py`, `_finalize_box_totals()` + `compute_sc_multileg()` |
| EQ-004 | Science Care cheapest-of-three rollup | `app/services/science_care_quote.py`, `_cheapest_for_leg()` |
| EQ-005 | Hotshot zone determination from miles | `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`; rate-set fallback in `app/services/hotshot_rates.py` |
| EQ-006 | Hotshot base rate (Zones A-J) | `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()` |
| EQ-007 | Hotshot base rate (Zone X) | `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()` |
| EQ-008 | Hotshot fuel surcharge | `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()` |
| EQ-009 | Hotshot dynamic VSC application | `app/quote/logic_hotshot.py` (applies); computed in `app/services/fuel_surcharge.py`, `get_vsc_pct_for_zone()` |
| EQ-010 | Hotshot quote total | `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()` |

## Documentation gaps

CLAUDE.md Section 6 calls out the following hotshot-adjacent formulas as
required documentation; they are NOT yet covered by an EQ entry and will
be tackled in follow-up PRs:

- Air freight rate calculation (dimensional weight x per-lb zone rate
  with weight breaks) — `app/quote/logic_air.py`.
- Dynamic VSC computation itself (EIA diesel price -> PADD region ->
  surcharge %) — `app/services/fuel_surcharge.py`,
  `get_vsc_pct_for_zone()` and the `vsc_matrix` AppSetting.
- Accessorial charge calculation (fixed vs. percentage; per-quote
  application) — `app/quotes/routes.py` and the `Accessorial` model.
- Beyond charge flat fee application — `app/services/beyond_rates.py`.
- Zone determination logic from ZIP code pairs (origin x destination ->
  cost zone) — `app/services/cost_zones.py` and the `CostZone` /
  `ZipZone` tables.

---

## EQ-001: Dimensional Weight

**Purpose:** Convert a box's interior cubic inches to billable pounds when the
volumetric weight exceeds the actual weight (Air freight industry standard).

**Formula:**

    dim_weight_lb = (length_in × width_in × height_in) / DIM_DIVISOR

For a leg with multiple boxes (Science Care path), this is summed per box and
multiplied by the count:

    dim_weight_lb = Σ_box ( L × W × H × count_box ) / DIM_DIVISOR

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `length_in` | float | inches | `SCBoxType.length_in` (SC) or quote payload (legacy) | Box interior length |
| `width_in` | float | inches | `SCBoxType.width_in` | Box interior width |
| `height_in` | float | inches | `SCBoxType.height_in` | Box interior height |
| `count_box` | int | count | Auto-allocator or user override | Number of boxes of that size in the leg |
| `DIM_DIVISOR` | const | (in³ / lb) | `app/services/constants.py` | 166 — IATA volumetric divisor for Air |

**Constraints:**

- Boxes whose dimensions are zero contribute 0 dim weight (and are skipped by
  the SC allocator so the freight isn't silently undercounted).
- Billable weight is `max(actual_weight, dim_weight)`; that comparison lives in
  the downstream `create_quote()` call, not here.

**Code location:** `app/services/science_care_quote.py`, `_finalize_box_totals()`
(~line 175); `DIM_DIVISOR` constant in `app/services/constants.py`.

**Worked example:** An X-Large box (52 × 20 × 15 in) holds 1 PELV03 (79 lb).

    dim_weight = (52 × 20 × 15) / 166 = 15,600 / 166 ≈ 93.98 lb

Billable weight = `max(79, 93.98) = 93.98 lb`.

**Last verified:** 2026-06-22

---

## EQ-002: Science Care Per-Tissue Box Allocation

**Purpose:** Pick the smallest-box-count packing for a given tissue qty out of
every box size the tissue can ship in.

**Formula:**

For each tissue row with `qty > 0`, the allocator considers every box code in
`SCTissueBoxCapacity` where `pieces_per_box > 0`:

    box_count(box) = ceil(qty / pieces_per_box(box))

    chosen_box = argmin_box ( box_count(box), interior_volume(box) )

Ties on `box_count` are broken by smaller interior volume (smaller box wins).
Zero-volume boxes (e.g. the `SMALL_AIRTRAY` placeholder until dimensions are
filled in) are skipped.

When the user overrides via the per-row dropdown (`box_choice_<leg>_<i>`), that
selection wins as long as the capacity table allows it.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `qty` | int | count | Tissue row form input | Pieces of this tissue on the leg |
| `pieces_per_box(box)` | int | count | `SCTissueBoxCapacity.pieces_per_box` | Capacity of that box for this tissue |
| `interior_volume(box)` | float | in³ | `SCBoxType.length_in × width_in × height_in` | Used only as a tie-breaker |
| `user_pick` | str | box code | Form field `box_choice_<leg>_<i>` | User dropdown override |

**Constraints:**

- A missing row OR `pieces_per_box <= 0` means the box cannot ship the tissue.
- A user override that points at a box not in the capacity table is ignored
  (allocator falls back to the recommendation).
- Tenants whose CSV upload predates the capacity table still have a single
  `default_box_type_code` + `pieces_per_box` on `SCTissueCode`; the allocator
  falls back to those values when the capacity index is empty for the tissue.

**Code location:** `app/services/science_care_quote.py`,
`recommended_box_for_qty()` (~line 218) and `allocate_boxes()` (~line 270).

**Worked example:** Tissue `ARM01` has capacities `{LRG: 7, XLG: 10}`. For
`qty = 8`:

    LRG: ceil(8/7) = 2 boxes
    XLG: ceil(8/10) = 1 box  ← winner (fewest boxes)

For `qty = 7`, both yield 1 box (tie); the smaller interior wins:

    LRG volume: 32 × 18 × 20 = 11,520 in³  ← winner (smaller)
    XLG volume: 52 × 20 × 15 = 15,600 in³

**Last verified:** 2026-06-22

---

## EQ-003: Science Care Leg Total Weight (with three-component breakdown)

**Purpose:** Compute the billable weight contribution of one shipment leg,
plus the three-component breakdown the results card surfaces (so the user can
see what is driving the leg's billable weight).

**Formula:**

    tissue_weight_lb     = Σ_tissue ( qty × unit_weight )
    box_tare_weight_lb   = Σ_box ( count_box × tare_weight )
    consumable_weight_lb = Σ_resolved_row ( weight_per_box × resolved_qty )

    leg_weight_lb = tissue_weight_lb + box_tare_weight_lb + consumable_weight_lb

`resolved_qty` follows a "blank input gets the temp_mode default; any typed
value (including 0) wins" rule. Concretely:

* `temp_mode = frozen` and the row is `consumable_type=dry_ice, scope=domestic`
  → default `resolved_qty = total_boxes` (one domestic dry ice per box).
* `temp_mode = rtu` and the row is `consumable_type=gel_pack, scope=domestic`
  → default `resolved_qty = total_boxes` (one domestic gel pack per box).
* Any other row → `resolved_qty = 0` unless the user types a Qty.
* Any non-blank `cons_qty_<leg>_<id>` value overrides the default, including
  a typed `0` (which suppresses the auto-default for that specific row).

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `qty` | int | count | Tissue row form input | Pieces of this tissue |
| `unit_weight` | float | lb | `SCTissueCode.unit_weight_lb` | Per-piece avg weight |
| `count_box` | int | count | Auto-allocator or override | Boxes of this size on the leg |
| `tare_weight` | float | lb | `SCBoxType.tare_weight_lb` | Empty-box weight |
| `weight_per_box` | float | lb | `SCConsumable.weight_lb_per_box` | Dry ice / gel pack added per box |
| `user_qty` | int | count | Form field `cons_qty_<leg>_<id>` | Per-consumable user quantity |

**Constraints:**

- Consumable Qty inputs follow the same "prefill blank only" semantic as the
  per-leg box-count overrides: blank → auto (temp_mode default for the
  matching row, 0 otherwise); any non-blank value wins exactly as typed,
  including `0` which suppresses the default for that row.
- The temp_mode auto-default only applies to the row that matches
  `(consumable_type, scope) ∈ {(dry_ice, domestic), (gel_pack, domestic)}`
  for `temp_mode ∈ {frozen, rtu}` respectively. Other consumables stay at 0
  unless the user types a Qty.
- A leg with `leg_weight_lb <= 0` is skipped (no quote attempted).
- Box overrides at the leg level (`box_count_<leg>_<box_id>`) replace the
  auto-allocator's per-tissue boxes; the tissue weight does NOT change, only
  the tare + dim totals.

**Code location:** `app/services/science_care_quote.py`,
`_finalize_box_totals()` (~line 175), `compute_leg_subtotals()` (live HTMX
helper for the form's Shipment-weight card), and the breakdown derivation in
`compute_sc_multileg()` (post-pricing results).

**Worked example:** Leg with one `PELV03` (79 lb) shipping in 1 X-Large box
(tare 14 lb), `temp_mode=frozen`, no consumable Qty inputs touched:

    tissue_weight_lb     = 1 × 79 = 79 lb
    box_tare_weight_lb   = 1 × 14 = 14 lb
    consumable_weight_lb = 1 × 25 = 25 lb  (auto: 1 domestic dry ice per box, 25 lb/box)
    leg_weight_lb        = 79 + 14 + 25 = 118 lb

Same leg with `temp_mode=rtu` (Ready to Use), gel pack at 20 lb/box:

    consumable_weight_lb = 1 × 20 = 20 lb  (auto: 1 domestic gel pack per box)
    leg_weight_lb        = 79 + 14 + 20 = 113 lb

If the user types `0` in the dry-ice Qty box to suppress the default (same
override semantic as the per-leg box-count inputs):

    consumable_weight_lb = 0 lb
    leg_weight_lb        = 79 + 14 + 0 = 93 lb

If the user types `3` to bump the dry-ice qty above the per-box default:

    consumable_weight_lb = 3 × 25 = 75 lb
    leg_weight_lb        = 79 + 14 + 75 = 168 lb

Each component lands on `LegResult.{tissue_weight_lb, consumable_weight_lb,
box_tare_weight_lb}` so the results card can render them as separate columns
and the three values always sum to `total_weight_lb` (asserted in
`test_leg_result_carries_weight_breakdown`). The live form uses
`compute_leg_subtotals()` to render the same three values + total in the
Shipment-weight card during data entry, before pricing runs.

**Last verified:** 2026-06-22

---

## EQ-004: Science Care Cheapest-of-Three Rollup

**Purpose:** Pick the winning freight option per leg out of Air, Hotshot, and
Established Lane (with the SC-to-SC routing override).

**Formula:**

    if routing_type == "SC to SC" AND established_rate > 0:
        winner = ("Established", established_rate)
    else:
        candidates = [c for c in (Air, Hotshot, Established) if c > 0]
        winner = ("min_label", min(candidates))

The SC-to-SC routing forces the pre-negotiated lane rate even when Air or
Hotshot would be cheaper, because SC-to-SC shipments contractually use the
established lane.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `Air` | float | USD | `create_quote(quote_type="Air").total` | Air freight total |
| `Hotshot` | float | USD | `create_quote(quote_type="Hotshot").total` | Hotshot total |
| `Established` | float | USD | `SCEstablishedLane.rate` (active today) | Pre-negotiated lab-to-lab rate |
| `routing_type` | str | enum | Form field `routing_type_<leg>` | One of `Outbound`, `SC to SC`, `Inbound` |

**Constraints:**

- Established lane rows are filtered by `effective_from <= today <= effective_to`
  (NULL bounds treated as open-ended); the lowest matching rate is used.
- A leg with no valid candidates contributes `$0` to the grand total but is
  flagged with a `skip_reason`.
- SC-to-SC fallback: if no established rate exists, the cheapest of Air /
  Hotshot is used instead so the leg still produces a freight cost.
- Established rate resolution (`_lookup_established`) tries `(origin_zip,
  dest_zip)` first. On a miss, it derives the leg's `(city, state)` from
  `Zipcode_Zones.csv` (via `app.services.zip_city_lookup`) and falls back
  to any lane row whose `dest_city` + `dest_state` match. This mirrors the
  source workbook's `lab_code + "City,State"` VLOOKUP so a different ZIP
  in the same metro still picks up the lane price. The exact ZIP match
  always wins when both rows exist.

**Code location:** `app/services/science_care_quote.py`,
`_cheapest_for_leg()` (~line 480) and `_lookup_established()` (~line 519);
ZIP→city helper in `app/services/zip_city_lookup.py`.

**Worked example:** Outbound leg, Air = $300, Hotshot = $250, Established
= $200:

    candidates = [Air 300, Hotshot 250, Established 200]
    winner = ("Established", 200)

Same totals but `routing_type = "SC to SC"`:

    winner = ("Established", 200)  # SC to SC always uses established when available

`routing_type = "SC to SC"` and `Established = None`:

    winner = ("Hotshot", 250)  # fall through to cheapest-of

**Last verified:** 2026-06-22 (added dest_city/dest_state fallback)

---

## EQ-005: Hotshot zone determination from miles

**Purpose:** Map the route distance to a Hotshot rate tier so the right
`HotshotRate` row is selected.

**Formula:**

    miles_int = ceil(get_distance_miles(origin, destination) or 0)
    zone      = get_hotshot_zone_by_miles(miles_int, rate_set)
    rate      = get_current_hotshot_rate(zone, rate_set)

The `HotshotRate` table is partitioned by `(rate_set, miles, zone)`. The
zone lookup returns Zones `A` through `J` for one-decade mile buckets
(A=1-9, B=10-19, ..., J=90-99) and Zone `X` for `miles >= 100`. The
`miles` parameter is integer-ceiled so 23.1 mi resolves to the 24-mi
row.

**Rate-set fallback:** when a user's `rate_set` has no matching row, the
service falls back to the row from `DEFAULT_RATE_SET`. See
`get_current_hotshot_rate()` (`app/services/hotshot_rates.py`, ~line 71)
and `_call_with_rate_set` (`app/services/rate_sets.py`, ~line 148). This
implements the precedence "customer-assigned rate set > default" — to
override pricing for a single customer, an admin creates a custom rate
set (any string), inserts rows for that rate set into the relevant
tables, then sets `User.rate_set` to that string on the customer's user
record.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `miles` | float | mi | `get_distance_miles(origin, destination)` | Routed distance between origin and destination ZIPs |
| `rate_set` | str | — | `User.rate_set` resolved at `app/quotes/routes.py:478` | Active named rate context |
| `zone` | str | — | `HotshotRate.zone` row for `(rate_set, miles)` | One of `A`-`J` or `X` |

**Constraints:**

- `miles` is `math.ceil`-ed before lookup.
- A missing rate-set row falls back to the `DEFAULT_RATE_SET` row.
- A missing default row raises (no silent zero — fail loud).

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~209-212; rate-set fallback in `app/services/hotshot_rates.py`,
`get_current_hotshot_rate()`, lines ~71-80.

**Worked example:** Origin "30301", destination "90808",
`get_distance_miles` returns 2188.4. `ceil(2188.4) = 2189`. The lookup
returns Zone `X` (any miles >= 100). The HotshotRate row for
(`DEFAULT_RATE_SET`, miles=100, zone=`X`) is then used for EQ-007.

**Last verified:** 2026-06-23

---

## EQ-006: Hotshot base rate (Zones A-J)

**Purpose:** Compute the pre-surcharge linehaul amount for short-haul
hotshot quotes (under 100 mi).

**Formula:**

    base = max(min_charge, weight * per_lb)

All three values come from the `HotshotRate` row selected by EQ-005. The
formula's `max(...)` form is mathematically equivalent to the Air-freight
"min_charge + (weight - weight_break) * per_lb" form **whenever**
`min_charge == weight_break * per_lb`. That invariant holds for every
row currently in `rates/Hotshot_Rates.csv` (Zone A: 382.5 x 0.208 = 79.56;
Zone B: 445 x 0.208 = 92.56; ... Zone J: 1975 x 0.208 = 410.8), which is
why the runtime can ignore `weight_break` without changing answers.

**Known wart:** `weight_break` is loaded from the rate row and surfaced
in the output dict under the `"weight_break"` key, but it does not
participate in the formula. If a future CSV edit decouples `min_charge`
from `weight_break * per_lb`, Hotshot will silently diverge from the
Air-style intent. A guard at CSV-import time is filed as a follow-up.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `weight` | float | lb | quote request | Billable shipment weight |
| `per_lb` | float | USD/lb | `HotshotRate.per_lb` | Per-pound rate for the zone |
| `min_charge` | float | USD | `HotshotRate.min_charge` | Minimum charge for the zone |
| `weight_break` | float | lb | `HotshotRate.weight_break` | Loaded and surfaced but not used in formula (see "Known wart") |

**Constraints:**

- All three values come from the same row. None are hardcoded.
- `per_mile` on the rate row is unused for Zones A-J (NULL in seed data).

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~222-225.

**Worked example:** Zone A row (`per_lb=0.208`, `min_charge=79.56`,
`weight_break=382.5`):

    weight=200 lb -> base = max(79.56, 200 * 0.208) = max(79.56, 41.60) = 79.56 USD
    weight=500 lb -> base = max(79.56, 500 * 0.208) = max(79.56, 104.00) = 104.00 USD

**Last verified:** 2026-06-23

---

## EQ-007: Hotshot base rate (Zone X)

**Purpose:** Compute the pre-surcharge linehaul amount for long-haul
hotshot quotes (>= 100 mi), where a per-mile minimum dominates instead
of a flat per-zone minimum.

**Formula:**

    min_charge = miles * per_mile
    base       = max(min_charge, weight * per_lb)

Both `per_lb` and `per_mile` come from the `HotshotRate` row selected by
EQ-005. The default-rate-set seed values are `per_lb = 5.1` and
`per_mile = 6.0192` (originally hardcoded in code; backfilled into the
DB by migration `a1c4d6e8f2b9_backfill_zone_x_per_mile.py`).

**Per-customer / per-rate-set overrides:** an admin can edit any
Zone X row at `/admin/hotshot_rates` and change `per_lb`, `per_mile`, or
`fuel_pct` for that `(rate_set, miles)` combination. To customize Zone X
pricing for one customer, create a new rate_set string (e.g.
`"acme_corp"`), insert a Zone X row under it, and set
`User.rate_set = "acme_corp"` on the customer's user record. The 2-tier
fallback in EQ-005 will pick up the customer-specific row; the default
row remains the fallback.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `miles` | int | mi | EQ-005 ceiled value | Integer miles for the trip |
| `weight` | float | lb | quote request | Billable shipment weight |
| `per_lb` | float | USD/lb | `HotshotRate.per_lb` | Per-pound rate (default seed 5.1) |
| `per_mile` | float | USD/mi | `HotshotRate.per_mile` | Per-mile minimum multiplier (default seed 6.0192) |

**Constraints:**

- `per_mile` **must be non-NULL** for any Zone X row. If NULL,
  `calculate_hotshot_quote` raises `ValueError` naming the offending
  `(rate_set, miles)` pair. The admin form
  (`app.admin.HotshotRateForm.validate`) enforces the same requirement
  at edit time, so a save with Zone selected = X and Per Mile blank
  fails before the row hits the DB.
- The CSV "MIN" cell for Zone X (currently 5.2) is loaded into
  `HotshotRate.min_charge` but is unused by the runtime — Zone X always
  uses `miles * per_mile` as its minimum. A CSV cleanup ticket tracks
  what to do with that cell.

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~217-225; data-integrity guard at lines ~218-221; admin-side
validator at `app/admin.py`, `HotshotRateForm.validate()`.

**Worked example:** Default-rate-set Zone X row (`per_lb=5.1`,
`per_mile=6.0192`):

    miles=150, weight=2000 lb
    min_charge = 150 * 6.0192 = 902.88 USD
    weight_cost = 2000 * 5.1 = 10200.00 USD
    base = max(902.88, 10200.00) = 10200.00 USD

    miles=150, weight=100 lb
    min_charge = 150 * 6.0192 = 902.88 USD
    weight_cost = 100 * 5.1 = 510.00 USD
    base = max(902.88, 510.00) = 902.88 USD

Same inputs against a customer-specific rate set with `per_mile = 8.0`:

    miles=150, weight=100 lb
    min_charge = 150 * 8.0 = 1200.00 USD
    base = max(1200.00, 510.00) = 1200.00 USD

**Last verified:** 2026-06-23

---

## EQ-008: Hotshot fuel surcharge

**Purpose:** Apply the per-rate-set fuel surcharge to the base linehaul
to produce the post-fuel subtotal.

**Formula:**

    fuel_amount    = base * fuel_pct
    base_with_fuel = base + fuel_amount

`fuel_pct` is sourced from `HotshotRate.fuel_pct` on the same row as
`per_lb` / `per_mile`, so it is already per-rate-set, per-zone-tier. The
default seed is `0.315` uniformly across every Zones A-J row and the
Zone X row.

**Per-customer override:** identical mechanism to EQ-007 — assign the
customer a custom `rate_set` whose rows have a different `fuel_pct`.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `base` | float | USD | EQ-006 or EQ-007 | Pre-surcharge linehaul |
| `fuel_pct` | float | fraction | `HotshotRate.fuel_pct` | Fuel surcharge expressed as a decimal (e.g. 0.315 = 31.5%) |

**Constraints:**

- `fuel_pct` defaults to `0.0` when the rate row's value is NULL (treated
  as "no surcharge configured"). This is intentional graceful behavior
  for partial data, not a fallback to a hidden constant.
- The fuel surcharge composes with the dynamic VSC (EQ-009), which uses
  `base_with_fuel` (post-fuel) as its base, NOT the pre-fuel `base`.

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~241-243.

**Worked example:** `base = 100`, `fuel_pct = 0.315`:

    fuel_amount    = 100 * 0.315 = 31.50 USD
    base_with_fuel = 100 + 31.50 = 131.50 USD

**Last verified:** 2026-06-23

---

## EQ-009: Hotshot dynamic VSC application

**Purpose:** Layer the dynamic, EIA-driven variable surcharge on top of
the post-fuel subtotal so the quote tracks live diesel prices without a
rate-table edit.

**Formula:**

    vsc_amount = base_with_fuel * dynamic_vsc_pct

`dynamic_vsc_pct` is computed by
`app.services.fuel_surcharge.get_vsc_pct_for_zone(dest_zone)` from the
current `FuelSurcharge` row (EIA diesel price + PADD region) and the
`vsc_matrix` AppSetting. **This entry documents only the application of
the VSC to the hotshot total** — the derivation of `dynamic_vsc_pct`
itself is a separate equation pending its own EQ entry (see
"Documentation gaps" above).

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `base_with_fuel` | float | USD | EQ-008 | Post-fuel subtotal |
| `dynamic_vsc_pct` | float | fraction | `get_vsc_pct_for_zone(dest_zone)` | Dynamic surcharge for the destination's VSC zone (e.g. 0.185) |

**Constraints:**

- VSC compounds onto the post-fuel subtotal, NOT the pre-fuel base.
- When destination ZIP cannot be resolved to a VSC zone,
  `_resolve_destination_zone` returns `"NATIONAL"` and emits
  warning metadata; the matrix lookup still applies if NATIONAL is
  defined in `vsc_matrix`.

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~234-244; VSC computation in `app/services/fuel_surcharge.py`,
`get_vsc_pct_for_zone()`.

**Worked example:** `base_with_fuel = 131.50`, `dynamic_vsc_pct = 0.185`:

    vsc_amount = 131.50 * 0.185 = 24.3275 USD

**Last verified:** 2026-06-23

---

## EQ-010: Hotshot quote total

**Purpose:** Combine the base linehaul, fuel surcharge, dynamic VSC, and
accessorial total into the final customer-facing quote.

**Formula:**

    quote_total = base + fuel_amount + vsc_amount + accessorial_total

Equivalent to `base_with_fuel + vsc_amount + accessorial_total`. The
order matters only for trace logging; the sum is commutative.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `base` | float | USD | EQ-006 or EQ-007 | Pre-surcharge linehaul |
| `fuel_amount` | float | USD | EQ-008 | Fuel surcharge dollars |
| `vsc_amount` | float | USD | EQ-009 | Dynamic VSC dollars |
| `accessorial_total` | float | USD | quote request (sum of selected `Accessorial` rows) | Sum of accessorial charges |

**Constraints:**

- All four components are independently non-negative.
- The `total_fsc_applied` field in the output dict is
  `(fuel_amount + vsc_amount) / base` and is purely informational — it
  is not used in any downstream computation.

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
line ~246.

**Worked example:** Zone A, weight = 20 lb, miles = 100, `per_lb = 5.0`,
`min_charge = 50.0`, `fuel_pct = 0.315`, `dynamic_vsc_pct = 0.185`,
`accessorial_total = 10`:

    base           = max(50.0, 20 * 5.0) = max(50.0, 100.0) = 100.00 USD
    fuel_amount    = 100.00 * 0.315      = 31.50 USD
    base_with_fuel = 100.00 + 31.50      = 131.50 USD
    vsc_amount     = 131.50 * 0.185      = 24.3275 USD
    quote_total    = 131.50 + 24.3275 + 10 = 165.8275 USD

This matches the assertion at `tests/test_logic_hotshot.py:40-75`.

**Last verified:** 2026-06-23
