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
| EQ-011 | Air cost-zone determination from ZIP pair | `app/quote/logic_air.py`, `calculate_air_quote()` (concat lookup); `get_zip_zone()` / `get_cost_zone()` |
| EQ-012 | Air freight base rate (per-lb with weight break) | `app/quote/logic_air.py`, `calculate_air_quote()`; rates seeded from FSI VSC-Locked workbook by `migrations/versions/f3a8c2b9d1e4_*.py` |
| EQ-013 | Beyond charge flat fee application | `app/quote/logic_air.py`, `get_beyond_rate()` and `calculate_air_quote()` |
| EQ-014 | Dynamic VSC computation (EIA diesel -> PADD -> surcharge %) | `app/services/fuel_surcharge.py`, `get_vsc_pct_for_zone()` |
| EQ-015 | Accessorial charge application | `app/quotes/routes.py`, `new_quote()`; canonical helper in `app/services/quote.py`, `create_quote()` |
| EQ-016 | Air quote total | `app/quote/logic_air.py`, `calculate_air_quote()` (+ Guarantee post-processing in `app/quotes/routes.py`) |
| EQ-017 | International freight base (workbook `R21`) | `app/services/international_quote.py`, `calculate_international_quote()`; lanes loaded by migration `migrations/versions/d8a4f9c1b2e6_*.py` |
| EQ-018 | International door-to-door km surcharge | `app/services/international_quote.py`, `calculate_international_quote()` |

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

**Formula** (matches FSI VSC-Locked workbook `Domestic Hotshot Quotes!D17`):

    if weight > weight_break:
        base = ((weight - weight_break) * per_lb) + min_charge
    else:
        base = min_charge

All three values come from the `HotshotRate` row selected by EQ-005. This
is the same weight-break shape Air uses (EQ-012). The earlier app
implementation used `max(min_charge, weight * per_lb)` which silently
diverged from the workbook whenever a row's `weight_break * per_lb` no
longer matched its `min_charge`; migration `c5d7f1e9a2b3` replaced the
rate card with the workbook's exact values and the runtime now uses the
workbook's formula directly.

**Rate-card source of truth:** the 10 `default`-rate-set rows in
`HotshotRate` for Zones A-J mirror `Domestic Hotshot Quotes!E45:G54` of
the FSI VSC-Locked workbook:

| Zone | Min      | Per Lb | Weight Break |
|------|---------:|-------:|-------------:|
| A    |  70.0128 | 0.2464 |  284.142857 |
| B    |  81.4528 | 0.2464 |  330.571429 |
| C    |  93.3504 | 0.2464 |  378.857143 |
| D    | 105.2480 | 0.2464 |  427.142857 |
| E    | 117.1456 | 0.2464 |  475.428571 |
| F    | 219.6480 | 0.2464 |  891.428571 |
| G    | 267.2384 | 0.2464 | 1084.571429 |
| H    | 267.2384 | 0.2464 | 1084.571429 |
| I    | 297.4400 | 0.2464 | 1207.142857 |
| J    | 361.5040 | 0.2464 | 1467.142857 |

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `weight` | float | lb | quote request | Billable shipment weight |
| `per_lb` | float | USD/lb | `HotshotRate.per_lb` | Per-pound rate above the break |
| `min_charge` | float | USD | `HotshotRate.min_charge` | Minimum charge (covers everything up to `weight_break`) |
| `weight_break` | float | lb | `HotshotRate.weight_break` | Pivot weight (`min_charge / per_lb`) |

**Constraints:**

- All three values come from the same row.
- `per_mile` on the rate row is unused for Zones A-J (NULL in seed data).
- When `weight_break` is NULL (legacy CSV uploads / admin form leave it
  optional), the runtime derives an effective break of `min_charge /
  per_lb` — same convention as the workbook's `G45 = F45/E45`. Without
  this fallback an A-J quote on such a row would flatten to `min_charge`
  regardless of weight, under-quoting heavy shipments.

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~244-252; migration `migrations/versions/c5d7f1e9a2b3_*`.

**Worked example:** Zone B row (`per_lb=0.2464`, `min_charge=81.4528`,
`weight_break=330.571`):

    weight=100 lb -> base = min_charge = 81.4528 USD
                    (100 <= 330.571)
    weight=612 lb -> base = ((612 - 330.571) * 0.2464) + 81.4528
                          = 281.429 * 0.2464 + 81.4528
                          = 69.344 + 81.4528 = 150.80 USD

**Last verified:** 2026-06-25

---

## EQ-007: Hotshot base rate (Zone X)

**Purpose:** Compute the pre-surcharge linehaul amount for long-haul
hotshot quotes (>= 100 mi), where the workbook switches to a pure
per-mile charge with NO weight floor and NO fuel surcharge.

**Formula** (matches FSI VSC-Locked workbook
`Domestic Hotshot Quotes!D18`, Zone X branch):

    base = miles * per_mile

`per_mile` is the only rate field that participates. The earlier app
implementation took `max(miles * per_mile, weight * per_lb)` (a defensive
floor that fired for very heavy loads on short long-haul trips) AND
applied the `fuel_pct` multiplier; both behaviors diverged from the
workbook. The workbook's `D18` is pure per-mile and the `Fuel` column for
Zone X is `0`. The runtime now mirrors that: no weight floor, fuel
multiplier short-circuited to `0` for Zone X. Migration
`c5d7f1e9a2b3` flips the seeded `HotshotRate('X').fuel_pct` from `0.315`
to `0.0` so per-customer rate sets aren't silently surcharged either.

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

**Worked example:** Default-rate-set Zone X row (`per_mile=6.0192`,
`fuel_pct=0.0`):

    miles=150, weight=2000 lb
    base = 150 * 6.0192 = 902.88 USD
    (heavy load does NOT escalate the base — no per-lb floor)

    miles=150, weight=100 lb
    base = 150 * 6.0192 = 902.88 USD

Same inputs against a customer-specific rate set with `per_mile = 8.0`:

    miles=150  ->  base = 150 * 8.0 = 1200.00 USD

**NYC override:** when the destination ZIP is in
`logic_hotshot.NYC_FLAT_RATE_ZIPS` (the workbook's `P3:P43` list — 38
Manhattan and outer-borough ZIPs), the runtime computes a parallel
`nyc_base = logic_hotshot.NYC_FLAT_RATE_USD` ($1,100) with no fuel
surcharge and picks `MAX(zone_base + fuel, nyc_base)`. This mirrors
`D20 = MAX(D17, D18)` in the workbook and applies whether the trip falls
in Zone X or one of the Zones A-J. Accessorials and VSC layer on top
either way.

**Last verified:** 2026-06-25

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

**Formula** (matches FSI VSC-Locked workbook
`Domestic Hotshot Quotes!K10 / K11 / D19`):

    vsc_zone   = max(origin_vsc_zone, dest_vsc_zone)        # workbook K10
    vsc_pct    = get_vsc_pct_for_zone(vsc_zone)             # workbook K11
    vsc_amount = base_with_fuel * vsc_pct                   # workbook D19

The picked VSC zone is the **larger** of origin and destination zones (an
integer 1-10 in the seeded `VscZone` table). When one endpoint resolves
to the non-numeric `"NATIONAL"` fallback, the numeric endpoint wins
(`_zone_sort_key` ranks `"NATIONAL"` below every real zone). Both
endpoints' VSC zones are surfaced in the result dict
(`origin_vsc_zone`, `dest_zone`) plus the picked one (`vsc_zone_used`)
so operators can audit which side drove the FSC.

`dynamic_vsc_pct` is computed by
`app.services.fuel_surcharge.get_vsc_pct_for_zone(vsc_zone)` from the
current `FuelSurcharge` row (EIA diesel price + PADD region) and the
`vsc_matrix` AppSetting — see EQ-014.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `base_with_fuel` | float | USD | EQ-008 | Post-fuel subtotal |
| `origin_vsc_zone` | str | — | `VscZone.vsc_zone` for origin ZIP (or `"NATIONAL"` fallback) | Origin VSC zone |
| `dest_vsc_zone` | str | — | `VscZone.vsc_zone` for destination ZIP (or `"NATIONAL"` fallback) | Destination VSC zone |
| `vsc_zone` | str | — | `max(origin_vsc_zone, dest_vsc_zone)` | The zone fed to EQ-014 |
| `dynamic_vsc_pct` | float | fraction | EQ-014 | Surcharge for the picked zone |

**Constraints:**

- VSC compounds onto the post-fuel subtotal, NOT the pre-fuel base.
- A Zone X / NYC quote has `base_with_fuel == base` because the runtime
  zeroes the fuel multiplier for those branches (EQ-007 / NYC override),
  so VSC effectively applies straight to the base in those scenarios —
  same shape as the workbook.
- When both endpoints fall back to `"NATIONAL"`, `vsc_zone_used` is
  `"NATIONAL"` and the FSC matrix lookup applies if that key is defined
  in `vsc_matrix`.

**Code location:** `app/quote/logic_hotshot.py`, `calculate_hotshot_quote()`,
lines ~265-295; VSC computation in `app/services/fuel_surcharge.py`,
`get_vsc_pct_for_zone()`.

**Worked example:** Origin VSC zone = 4 (UT/CO/SC), destination VSC zone
= 9 (CA/HI), `base_with_fuel = 131.50`, `vsc_pct(9) = 0.22`:

    vsc_zone   = max("4", "9") = "9"
    vsc_amount = 131.50 * 0.22 = 28.93 USD

If origin = 9 (CA) and destination = 4 (UT), the same `max` would still
pick `"9"` and apply 22 % — i.e. CA origins drag every destination's FSC
up, mirroring the workbook.

**Last verified:** 2026-06-25

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

**Worked example** (matches the FSI workbook's cached `D20` for the
default test scenario, origin 85022 → dest 85260, weight 100 lb,
Specific Time + VSC selected):

    Zone B: per_lb=0.2464, min=81.4528, WB=330.571, fuel_pct=0.315
    miles=15.83 -> ceil=16 -> Zone B
    weight (100) <= WB (330.571) -> base = min = 81.4528
    fuel_amount    = 81.4528 * 0.315 = 25.6576 USD
    base_with_fuel = 81.4528 + 25.6576 = 107.1104 USD
    accessorial    = 95.00 USD       (Specific Time)
    vsc_zone       = max(8, 8) = 8 -> vsc_pct = 0.195
    vsc_amount     = 107.1104 * 0.195 = 20.8866 USD
    quote_total    = 107.1104 + 20.8866 + 95.00 = $222.997

That is `Domestic Hotshot Quotes!D20` to the penny.

**Last verified:** 2026-06-25

---

## EQ-011: Air cost-zone determination from ZIP pair

**Purpose:** Map an origin/destination ZIP pair to the single Air `cost_zone`
string used to pick a rate row from `AirCostZone`.

**Formula:**

    orig_dest_zone = int(ZipZone.dest_zone for origin_zip)
    dest_dest_zone = int(ZipZone.dest_zone for destination_zip)
    concat         = f"{orig_dest_zone}{dest_dest_zone}"

    cost_zone_row = CostZone where CostZone.concat == concat
                    OR (fallback) CostZone where CostZone.concat == f"{dest_dest_zone}{orig_dest_zone}"

    cost_zone     = cost_zone_row.cost_zone

The `ZipZone.dest_zone` integer is the Air-routing zone for that ZIP (NOT the
VSC zone — VSC zones live in `VscZone`, see EQ-014). The concatenation key is
the string representation of the two zone integers glued together (e.g. zones
`3` and `7` produce `"37"`).

**Reverse-pair fallback:** when the forward `concat` has no `CostZone` row,
the lookup retries with the zones reversed. This lets a CSV that only defines
one direction of a lane (e.g. `"37"` is mapped but not `"73"`) still resolve.
The reverse hit is treated as equivalent — both `(o, d)` and `(d, o)` route to
the same `cost_zone`. If neither direction is mapped, the quote returns an
error result with `cost_zone` unresolved.

**Rate-set fallback:** `ZipZone`, `CostZone`, and `AirCostZone` lookups all
flow through `query_with_rate_set_fallback`, which tries the caller's named
rate set first and falls back to `DEFAULT_RATE_SET` when no row matches —
same precedence as the hotshot tables (EQ-005).

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `origin_zip` | str | ZIP | quote request | 5-digit origin ZIP code |
| `destination_zip` | str | ZIP | quote request | 5-digit destination ZIP code |
| `ZipZone.dest_zone` | int | — | `ZipZone` row for ZIP | Air-routing zone number for that ZIP |
| `CostZone.concat` | str | — | `CostZone` row keyed by joined zones | Concatenated `"{orig}{dest}"` lookup key |
| `cost_zone` | str | — | `CostZone.cost_zone` | Resulting Air cost-zone code consumed by EQ-012 |
| `rate_set` | str | — | `User.rate_set` (via `app/quotes/routes.py`) | Active named rate context |

**Constraints:**

- Origin and destination ZIPs are normalized to 5 digits via
  `_normalize_zip_lookup_key()`; ZIP+4 inputs (`12345-6789`) are truncated to
  the leading 5 digits. Malformed inputs return `None`, which surfaces as a
  ZIP-not-found error.
- A `ZipZone` row missing `dest_zone` or `beyond` fails the lookup with an
  explicit error (no silent zero-zoning).
- A `CostZone` miss in both directions returns
  `f"Cost zone not found for concatenated zone {concat} or {reverse_concat}"`.

**Code location:** `app/quote/logic_air.py`,
`calculate_air_quote()` lines ~240-270; ZIP normalization at
`_normalize_zip_lookup_key()` (~line 38); `ZipZone` model at
`app/models.py:397`; `CostZone` model at `app/models.py:422`.

**Worked example:** Origin ZIP `30301` resolves to `ZipZone.dest_zone = 3`;
destination ZIP `90808` resolves to `ZipZone.dest_zone = 7`.

    concat        = "37"
    cost_zone_row = CostZone(concat="37").first()
    cost_zone     = cost_zone_row.cost_zone        # e.g. "C"

If `CostZone("37")` is absent but `CostZone("73")` exists, the fallback
returns the `"73"` row's `cost_zone` value instead.

**Last verified:** 2026-06-23

---

## EQ-012: Air freight base rate (per-lb with weight break)

**Purpose:** Compute the pre-surcharge linehaul amount for an Air quote from
the `AirCostZone` row selected by EQ-011.

**Formula:**

    if billable_weight > weight_break:
        base = min_charge + (billable_weight - weight_break) * per_lb
    else:
        base = min_charge

`billable_weight` is `max(actual_weight, dim_weight)` — dim weight comes from
EQ-001 (`L * W * H * pieces / DIM_DIVISOR`) and the larger of the two wins.
The route handler does that comparison before invoking `calculate_air_quote`,
so the air quote always receives the already-resolved billable weight.

The three rate values (`min_charge`, `per_lb`, `weight_break`) are all read
from the SAME `AirCostZone` row keyed by `(rate_set, cost_zone)` — so changing
a cost zone's pricing is a single-row admin edit, and per-customer overrides
work via the rate-set mechanism (same as hotshot, EQ-005/EQ-007).

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `billable_weight` | float | lb | `max(weight_actual, weight_dim)` resolved in `new_quote()` | Larger of actual vs. dimensional weight |
| `weight_break` | float | lb | `AirCostZone.weight_break` | Pivot weight; `min_charge` covers everything up to here |
| `per_lb` | float | USD/lb | `AirCostZone.per_lb` | Per-pound rate above the weight break |
| `min_charge` | float | USD | `AirCostZone.min_charge` | Minimum charge for the zone (covers weight up to and including `weight_break`) |

**Constraints:**

- The break point is **inclusive at the minimum**: `weight == weight_break`
  resolves to `base = min_charge` (the strict `>` branch fires only above the
  break).
- All three values come from the same row; none are hardcoded.
- A missing `AirCostZone` row for the resolved `cost_zone` raises an explicit
  error result (no silent zero).
- Unlike hotshot (EQ-006), the formula DOES use `weight_break` — Air rates
  legitimately decouple `min_charge` from `weight_break * per_lb`.

**Code location:** `app/quote/logic_air.py`, `calculate_air_quote()`,
lines ~276-283.

**Rate-card source of truth:** the eight `default` rate-set rows in
`AirCostZone` mirror ``Domestic Air Quotes!C4:E11`` of the FSI Shipping
Quote Tool 2026 VSC-Locked workbook. The rates are reproduced verbatim in
``rates/air_cost_zone.csv`` and re-applied to the DB by migration
``f3a8c2b9d1e4``:

| Zone | Min      | Per Lb     | Weight Break |
| ---- | -------- | ---------- | ------------ |
| A    | 235.7555 | 1.4551680  | 162.012579   |
| B    | 222.9976 | 1.5832960  | 140.843931   |
| C    | 248.5134 | 1.7571840  | 141.427083   |
| D    | 286.7779 | 1.9402240  | 147.806604   |
| E    | 331.4305 | 2.0592000  | 160.951111   |
| F    | 407.9687 | 2.1873280  | 186.514644   |
| G    | 427.1055 | 2.2422400  | 190.481633   |
| H    | 465.3792 | 2.4893440  | 186.948529   |

`weight_break` is stored as an absolute value (= ``min_charge / per_lb`` per
the workbook formula at ``E4:E11``) so the runtime does not have to recompute
it. Per-customer rate sets that need to deviate from this card are stored
as separate rows under their own ``rate_set`` value.

**Worked example:** Cost zone `B` row (FSI VSC-Locked values:
`min_charge=222.997632`, `per_lb=1.5832960`, `weight_break=140.843931`):

    billable_weight=100 lb -> base = 222.997632 USD
                              (100 <= 140.843931, min_charge alone)

    billable_weight=612 lb -> base = 222.997632
                                   + (612 - 140.843931) * 1.5832960
                                   = 222.997632 + 745.979823
                                   = 968.977455 USD

    billable_weight=140.843931 lb -> base = 222.997632 USD
                                     (exactly at the break, min_charge wins)

**Last verified:** 2026-06-25

---

## EQ-013: Beyond charge flat fee application

**Purpose:** Add the flat "beyond" surcharge for origin and/or destination
ZIPs that sit outside standard delivery areas (e.g. remote / island /
outlying routes).

**Formula:**

    origin_beyond = parse(ZipZone.beyond for origin_zip)   # token or None
    dest_beyond   = parse(ZipZone.beyond for destination_zip)

    origin_charge = BeyondRate.rate where zone == origin_beyond   else 0.0
    dest_charge   = BeyondRate.rate where zone == dest_beyond     else 0.0

    beyond_total  = origin_charge + dest_charge

Both endpoints are scanned independently and their flat fees added — a
shipment that is beyond at both ends pays both charges. The fee is a flat
dollar amount per endpoint, NOT a per-pound or per-mile charge.

**`ZipZone.beyond` parsing:** the raw `beyond` column holds an indicator
string (e.g. `"BEY G"` or `"N/A"`). `_parse_beyond()` rules:

- `None`, empty, `"N/A"`, `"NO"`, `"NONE"`, `"NAN"` → `None` (no charge).
- Anything else → the last whitespace-separated token, uppercased
  (e.g. `"BEY G"` → `"G"`). That token is the lookup key for `BeyondRate.zone`.

**Rate-set fallback:** `BeyondRate` rows are scoped by `rate_set`; lookup
uses `query_with_rate_set_fallback`, so a custom rate set falls back to
`DEFAULT_RATE_SET` rows that are not overridden.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `ZipZone.beyond` | str / NULL | — | `ZipZone` row for ZIP | Raw beyond indicator (e.g. `"BEY G"`, `"N/A"`) |
| `origin_beyond` | str / None | — | `_parse_beyond(origin_row.beyond)` | Normalized lookup key, or `None` if not beyond |
| `dest_beyond` | str / None | — | `_parse_beyond(dest_row.beyond)` | Normalized lookup key, or `None` if not beyond |
| `BeyondRate.rate` | float | USD | `BeyondRate` row keyed by `(rate_set, zone)` | Flat dollar surcharge for the beyond code |
| `beyond_total` | float | USD | `origin_charge + dest_charge` | Sum of both endpoints, fed into EQ-016 |

**Constraints:**

- An unrecognized beyond code OR a missing `BeyondRate` row for the code
  resolves to `0.0` — beyond fees never fail the quote, they just contribute
  zero.
- `BeyondRate.up_to_miles` exists on the model but the air pricing code does
  NOT consult it (see "Known wart" below).
- `beyond_total` participates in the fuel surcharge base (EQ-016), so a
  beyond fee is itself surcharged by the VSC.

**Known wart:** `BeyondRate.up_to_miles` is a column on the model and is
populated by the CSV importer, but `get_beyond_rate()` ignores it — every
matching `BeyondRate` row applies its flat `rate` regardless of trip
distance. If a future beyond schedule needs distance-tiered beyond fees,
the lookup must change AND this entry must update.

**Code location:** `app/quote/logic_air.py`, `get_beyond_rate()` (~line 143)
and `calculate_air_quote()` lines ~285-302; `_parse_beyond()` defined inline
at ~line 285; `BeyondRate` model at `app/models.py:364`.

**Worked example:** Origin ZIP has `ZipZone.beyond = "BEY G"`; destination
ZIP has `ZipZone.beyond = "N/A"`. `BeyondRate("G").rate = 75.00` in the
default rate set.

    origin_beyond = "G"
    dest_beyond   = None
    origin_charge = 75.00 USD
    dest_charge   = 0.00 USD
    beyond_total  = 75.00 USD

Both endpoints beyond (origin `"G"`=75, destination `"H"`=125):

    beyond_total  = 75.00 + 125.00 = 200.00 USD

**Last verified:** 2026-06-23

---

## EQ-014: Dynamic VSC computation (EIA diesel -> PADD -> surcharge %)

**Purpose:** Derive the live Variable Surcharge percentage from the current
EIA diesel price for the destination zone's PADD region. This drives Air's
fuel surcharge (origin's VSC, see EQ-016) and is the same source called by
the hotshot path (EQ-009 *applies* this value).

**Formula:**

    matrix       = parse(AppSetting["vsc_matrix"])          # list of tier dicts
    zones        = parse(AppSetting["vsc_zones"])           # dict zone -> region
    region       = zones.get(dest_zone) or "NATIONAL"
    fuel_row     = FuelSurcharge where padd_region == region
                   OR (fallback) FuelSurcharge where padd_region == "NATIONAL"
    diesel_price = fuel_row.current_rate                    # USD/gallon
    vsc_pct      = tier.pct for first tier where tier.min <= diesel_price < tier.max
                   OR 0.0 if no tier matches

The matrix is a JSON array of `{min, max, pct}` objects expressing diesel
$/gallon ranges that map to a surcharge percentage. The first matching tier
wins (no interpolation). The match is half-open: `min` is inclusive, `max`
is exclusive.

**Zone-to-PADD resolution (`resolve_padd_region`):** `vsc_zones` is a JSON
dict like `{"1": "PADD1", "7": "PADD4"}`. The lookup tries the raw zone
string first; if the raw string is all digits, it also tries the
leading-zero-stripped form (`str(int(raw_zone))`, so `"09"` is retried as
`"9"`). Either form resolves to the same PADD region. Any zone absent from
both forms falls back to `"NATIONAL"`.

**Failure-mode policy (all return `0.0`):**

- `vsc_matrix` AppSetting missing or unparseable.
- `FuelSurcharge` row missing for both the region AND `NATIONAL`.
- `diesel_price` falls outside every matrix tier.
- Any `SQLAlchemyError` during lookup.
- No active Flask app context (e.g. offline unit tests).

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `dest_zone` | str | — | Caller (e.g. VSC zone from `VscZone`) | Zone identifier to map to a PADD region |
| `AppSetting["vsc_matrix"]` | JSON | — | `AppSetting` row | Array of `{min, max, pct}` diesel tiers |
| `AppSetting["vsc_zones"]` | JSON | — | `AppSetting` row | Mapping of zone codes to PADD region names |
| `FuelSurcharge.current_rate` | float | USD/gal | EIA-sourced row, refreshed by `scripts/sync_eia_rates.py` | Current diesel price for the PADD region |
| `vsc_pct` | float | fraction | Matrix tier `pct` | Returned surcharge as a decimal (0.185 = 18.5%) |

**Constraints:**

- The matrix interval is `[min, max)` — diesel exactly at `min` matches that
  tier; diesel exactly at `max` falls through to the next tier (or to 0.0 if
  it is also above the topmost `max`).
- "Never blocks a quote" — every failure path returns `0.0` and logs.
  Operationally that means a misconfigured matrix silently zeros the VSC; the
  guardrail is the warning log emitted in each branch.
- `FuelSurcharge` rows are kept current by `scripts/sync_eia_rates.py` (run
  ad-hoc or via a scheduled job); stale rows continue to return whatever the
  last sync wrote.

**Code location:** `app/services/fuel_surcharge.py`,
`get_vsc_pct_for_zone()` (~line 95), `resolve_padd_region()` (~line 40),
`lookup_matrix_pct()` (~line 72); `FuelSurcharge` model at
`app/models.py:469`; sync script at `scripts/sync_eia_rates.py`.

**Worked example:** Destination zone `"7"`, `vsc_zones = {"7": "PADD4"}`,
`FuelSurcharge(padd_region="PADD4").current_rate = 5.123` USD/gal, and
`vsc_matrix` includes `[{"min": 5.0, "max": 5.5, "pct": 0.185}, ...]`.

    region       = "PADD4"
    diesel_price = 5.123
    matched_tier = {"min": 5.0, "max": 5.5, "pct": 0.185}
    vsc_pct      = 0.185           # 18.5%

Zone `"99"` (not in mapping):

    region       = "NATIONAL"      # fallback
    -> diesel price comes from the NATIONAL FuelSurcharge row instead

Diesel price `3.40` with matrix tiers all starting at `4.00`:

    matched_tier = None
    vsc_pct      = 0.0             # logs a warning, quote continues

**Last verified:** 2026-06-23

---

## EQ-015: Accessorial charge application

**Purpose:** Sum the selected per-quote accessorial charges into the
`accessorial_total` that EQ-010 (hotshot) and EQ-016 (air) fold into the
final quote total.

**Formula:**

For each selected accessorial name, look up the `Accessorial` row by name
(case-insensitive), then accumulate:

    accessorial_total = Σ_selected ( Accessorial.amount )

The "Guarantee" accessorial is special-cased and applied AFTER the freight
quote computes its base + surcharges. For Air quotes:

    if "guarantee" in selected_names:
        guarantee_cost     = (quote_total - other_accessorial_total) * 0.25
        accessorial_total += guarantee_cost
        quote_total       += guarantee_cost

`quote_total - other_accessorial_total` is the **freight subtotal** for Air:
linehaul + beyond + fuel surcharge (everything except other accessorials).
Guarantee is therefore 25% of the surcharged freight, NOT just the
pre-surcharge linehaul. The comment in `new_quote()` calls it "linehaul and
beyond" — that wording is imprecise; the fuel surcharge is included.

**`is_percentage` is NOT honored by either pricing path.** The
`Accessorial.is_percentage` column exists on the model and is loaded into the
in-memory `_AccessorialRow` snapshot (`app/services/quote.py`, line ~61), but
**neither `app/quotes/routes.py:new_quote()` nor
`app/services/quote.py:create_quote()` ever inspects the flag when computing
the dollar total.** The percentage branch in both paths is triggered solely by
a case-insensitive substring match on `"guarantee"` in the accessorial's
display name. The only consumer of `is_percentage` today is the SC display
helper `app/science_care/routes.py:_format_accessorial_cost()`, which uses
it to render the label as `"25%"` vs. `"$25.00"` in the form UI — it does not
affect pricing.

**Two implementations:** the two code paths that apply accessorials are
intentionally similar but differ in how the Guarantee percentage value is
sourced:

| Path | Where | Fixed-accessorial handling | Guarantee handling |
| --- | --- | --- | --- |
| Web form | `app/quotes/routes.py`, `new_quote()` (~lines 460-540) | `amount` treated as USD; `is_percentage` ignored | Hardcoded 25% (Air only); name-matched on `"guarantee"` substring; `amount` ignored |
| Canonical service | `app/services/quote.py`, `create_quote()` (~lines 230-278) | `amount` treated as USD; `is_percentage` ignored | Name-matched on `"guarantee"` substring; `Accessorial.amount` interpreted as the percentage value (e.g. `25.0` -> 25%); defaults to 25.0 if zero; applied to BOTH Air and Hotshot |

The web-form path is what hits the database for interactive submissions; the
service path is invoked by the JSON API and the Science Care multi-leg flow.
Both produce the same Guarantee = 25% behavior for Air today, because the
seeded `Accessorial("Guarantee")` row stores `amount = 25.0` (which the
service path uses) and the web-form path hardcodes the same `0.25` constant.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `selected_names` | list[str] | — | Form `accessorials` field / JSON `accessorials` array | Display names of selected accessorials |
| `Accessorial.amount` | float | USD or % | `Accessorial` row | Flat dollar amount for non-Guarantee accessorials; for the Guarantee row the service path reinterprets it as a percentage value (e.g. `25.0` -> 25%) |
| `Accessorial.is_percentage` | bool | — | `Accessorial` row | Display-only today; ignored by both pricing paths (see note above). Used only by `app/science_care/routes.py:_format_accessorial_cost()` to render `"25%"` vs `"$25.00"` |
| `quote_total` | float | USD | `calculate_air_quote()` / `calculate_hotshot_quote()` | Pre-guarantee quote total returned by pricing |
| `accessorial_total` | float | USD | running sum | Accumulated across fixed accessorials + Guarantee |

**Constraints:**

- Unknown accessorial names (no matching `Accessorial` row, case-insensitive)
  are silently dropped — they do not contribute to `accessorial_total` and do
  not error out the quote.
- Guarantee is applied to Air-only in the web-form path (Hotshot ignores it).
  The service path applies it to both quote types — a minor divergence;
  Hotshot guarantees are currently filtered out at the option-list level
  (`get_accessorial_options("Hotshot")` strips any name containing
  "guarantee") so this branch is unreachable from the UI today.
- Accessorial selection is a flat list of names — there is no per-quote
  override of the amount; the seeded `Accessorial.amount` is authoritative.
- The cached `Accessorial` rows live in `_accessorial_cache()`
  (`app/quotes/routes.py`, 1-entry LRU) and `_get_accessorial_rows()`
  (`app/services/quote.py`, 24-hour TTL). Admin edits must call
  `clear_accessorial_cache()` for the web path; the service path picks up
  changes on the next TTL refresh.

**Known wart:** `Accessorial.is_percentage` is admin-editable via
`/admin/accessorials` and exposed on the model, but neither pricing path
reads it. Both paths use a hardcoded `"guarantee"` substring test to decide
whether to apply percentage semantics. If an admin adds a new
percentage-type accessorial whose name does not contain `"guarantee"`, both
the web form and the service path will charge its `amount` as flat USD —
the `is_percentage=True` flag has no effect on the bill. The flag only
changes how the SC form labels the accessorial in the UI (`"5%"` vs
`"$5.00"`). A unified policy that honors `is_percentage` for arbitrary
accessorial names is filed as a follow-up.

**Code location:** `app/quotes/routes.py`, `new_quote()` lines ~460-540
(fixed sum + Air-only Guarantee post-processing); `app/services/quote.py`,
`create_quote()` lines ~230-278 (alternative path used by JSON/SC);
accessorial cache in `app/quotes/routes.py:_accessorial_cache()` (~line 146);
`Accessorial` model at `app/models.py:323`.

**Worked example:** Air quote, `quote_total = 500.00` returned from
`calculate_air_quote` (which already includes the FSC), other accessorials
`"4hr Window" = $50` and `"Weekend" = $125` (already inside the 500). The
user also selected `"Guarantee"`.

    fixed_accessorial_total = 50 + 125 = 175.00 USD
    accessorial_total       = 175.00 USD
    quote_total (from air)  = 500.00 USD       # base + beyond + fsc + 175

    linehaul_with_beyond_and_fsc = 500.00 - 175.00 = 325.00 USD
    guarantee_cost               = 325.00 * 0.25  = 81.25 USD

    accessorial_total = 175.00 + 81.25 = 256.25 USD
    quote_total       = 500.00 + 81.25 = 581.25 USD

Hotshot quote, same accessorials but no Guarantee (web-form path strips it):

    accessorial_total = 50 + 125 = 175.00 USD
    # Guarantee never appears in Hotshot's option list, so it is not applied.

**Last verified:** 2026-06-23

---

## EQ-016: Air quote total

**Purpose:** Combine the base linehaul, beyond charges, dynamic VSC, and
accessorial total into the final customer-facing Air quote.

**Formula:**

    total_base_freight = base + beyond_total
    fsc_pct            = dest_vsc_pct                  # destination, per FSI workbook
    fsc_amount         = total_base_freight * fsc_pct
    quote_total        = total_base_freight + fsc_amount + accessorial_total

Guarantee is then folded in by the route handler per EQ-015 (Air only).

**Origin vs destination VSC:** Air uses the DESTINATION ZIP's VSC zone to
derive `fsc_pct`. This mirrors the FSI Shipping Quote Tool 2026 VSC-Locked
workbook, where ``Domestic Air Quotes!U5`` is
``VLOOKUP($O$4 [destination zip], 'VSC Zones', 4)`` and the resulting zone
feeds ``U9`` (FSC %). `surcharge_policy = "destination_zone_fsc"` in the
result dict. Hotshot also uses the destination VSC zone (EQ-009), so both
quote types are now consistent. The origin's VSC percentage is still computed
and surfaced in the result as `origin_vsc_pct` for transparency, but it does
not enter the dollar math.

**VSC base includes beyond:** the fuel surcharge is applied to
`base + beyond_total`, not to `base` alone — so a beyond-fee endpoint is
itself surcharged. This matches the FSI workbook's
``VSC = (Subtotal - Σaccessorials) * FSC%`` line at ``O21``. Accessorials are
NOT surcharged.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `base` | float | USD | EQ-012 | Pre-surcharge linehaul |
| `beyond_total` | float | USD | EQ-013 | Origin + destination flat beyond fees |
| `dest_vsc_pct` | float | fraction | EQ-014 with `dest_zone = VscZone(destination).vsc_zone` | Destination's dynamic VSC percentage |
| `accessorial_total` | float | USD | EQ-015 (pre-Guarantee) | Sum of selected accessorials |

**Constraints:**

- The origin and destination VSC zones come from `VscZone` (not `ZipZone`) —
  the two tables are intentionally split so Air-routing zones and surcharge
  zones can evolve independently.
- A missing `VscZone` row for either endpoint fails the quote with an
  explicit error ("missing valid vsc_zone"). The Air path does NOT silently
  apply 0.0 — the origin VSC zone is still resolved so the result dict can
  surface `origin_vsc_pct` for operator reference even though it is not used
  in the dollar math.
- `total_fsc_applied` is set to `fsc_pct` (the same value used in the dollar
  computation); it is purely informational.
- **Rate-card source of truth:** the eight `AirCostZone` rows in the
  `default` rate set are the FSI Shipping Quote Tool 2026 VSC-Locked
  workbook's ``Domestic Air Quotes!C4:E11`` rates (zones A-H). The
  ``f3a8c2b9d1e4`` migration rewrites them on `flask db upgrade head`; any
  per-customer rate set that needs to track the public card must be reviewed
  separately.

**Code location:** `app/quote/logic_air.py`, `calculate_air_quote()`,
lines ~304-329; Air-only Guarantee post-processing in
`app/quotes/routes.py:new_quote()` (~lines 534-540); migration at
`migrations/versions/f3a8c2b9d1e4_align_air_cost_zones_with_fsi_vsc_locked.py`.

**Worked example (matches the FSI VSC-Locked tool for SCTX → SLC at 612 lb,
all six accessorials selected, VSC on):** Cost zone `B`
(`min_charge=222.997632`, `per_lb=1.5832960`, `weight_break=140.843931`).
Beyond both endpoints = 0. Destination VSC zone = 7 → `dest_vsc_pct = 0.175`.
`accessorial_total = 50 + 95 + 110 + 125 + 125 + 75 = 580`.

    base               = 222.997632 + (612 - 140.843931) * 1.5832960
                       = 222.997632 + 471.156069 * 1.5832960
                       = 222.997632 + 745.979823
                       = 968.977455 USD                              (EQ-012)
    beyond_total       = 0.00                                        (EQ-013)
    total_base_freight = 968.977455 + 0                = 968.977455 USD
    fsc_amount         = 968.977455 * 0.175            = 169.571055 USD
    quote_total        = 968.977455 + 169.571055 + 580 = 1,718.55 USD

That matches the FSI workbook's `Domestic Air Quotes!O22` for the same
inputs.

**Last verified:** 2026-06-25

---

## EQ-017: International freight base (workbook `R21`)

**Purpose:** Compute the pre-surcharge freight charge for an international
shipment from one of the seven SC labs to a pre-negotiated destination
city.

**Formula** (matches FSI VSC-Locked workbook `International Quotes!R21`):

    if weight > weight_break and weight_break > 0:
        base = ((weight - weight_break) * per_lb) + min_charge
    else:
        base = min_charge

All three values come from the resolved `SCInternationalLane` row keyed
by `(destination_display_string, lab_code, rate_set)`. The lane table
seeds from `rates/international_lanes.csv` (1,099 rows, all `Standard`
class today; the workbook supports `Customer Specific` rates but none
are populated).

**No VSC, no accessorials, no fuel surcharge.** The workbook prices
these lanes net (one of the reasons quotes above the EQ-018 surcharge
threshold require operator confirmation per `Z11`).

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `weight` | float | lb | quote request | Billable shipment weight |
| `per_lb` | float | USD/lb | `SCInternationalLane.per_lb` | Per-pound rate above the break |
| `min_charge` | float | USD | `SCInternationalLane.min_charge` | Flat minimum covering everything up to the break |
| `weight_break` | float | lb | `SCInternationalLane.weight_break` | `min_charge / per_lb` (computed by workbook `M = K/L`) |

**Constraints:**

- A missing lane returns an error result; the caller is expected to fall
  back to "Contact FSI for Quote" (workbook's `R21` error path).
- `weight_break` is stored as an absolute value so the runtime does not
  recompute it. When the CSV cell was blank on export, the importer
  backfills `min_charge / per_lb` to match the workbook formula.

**Code location:** `app/services/international_quote.py`,
`calculate_international_quote()` lines ~95-115; lane model at
`app/models.py:SCInternationalLane`; seed migration
`migrations/versions/d8a4f9c1b2e6_*.py`.

**Worked example:** `Australia - Adelaide` lane for `SCAZ`
(`min=2950, per_lb=10.5, weight_break=280.95`):

    weight=272 lb -> base = min = 2,950.00 USD       (272 <= 280.95)
    weight=500 lb -> base = ((500 - 280.95) * 10.5) + 2950.00
                          = 219.05 * 10.5 + 2950.00
                          = 2,300.00 + 2,950.00 = 5,250.00 USD

**Last verified:** 2026-06-25

---

## EQ-018: International door-to-door km surcharge

**Purpose:** Apply the workbook's `AA10` "international hotshot" charge
on top of EQ-017 when a Door-to-Door Standard lane's destination city is
more than 80 km from the airport.

**Formula** (matches FSI VSC-Locked workbook `International Quotes!AA10`):

    if (notes == "Door to Door"
            and rate_class == "Standard"
            and cost_per_km_over_80 is not None
            and km_to_airport > 80):
        intl_hotshot_surcharge = (round(km_to_airport) - 80)
                               * cost_per_km_over_80
    else:
        intl_hotshot_surcharge = 0

The km is the distance from the destination city to the lane's airport
(workbook `AA8 = MIN(W9:W18)` where rows 9-18 are Google Distance Matrix
lookups for each of the lane's `Airport Code 1/2/3` options). The
runtime resolves this automatically when `km_to_airport` is not passed
in: `app.quote.distance.get_km_to_nearest_airport()` calls Google
Directions for each of the lane's 1-3 candidate airports
(`"{IATA} Airport"` → `"City of {city}, {country}"`) and picks the
smallest km value. Callers can still override with `km_to_airport=...`
to skip the network call (useful for tests and for cases where ops
already knows the right distance). The picked IATA code is surfaced on
the result as `InternationalQuote.picked_airport`.

Auto-resolve preconditions (skipped otherwise — saves a Google call):
the lane must be Door-to-Door Standard with a non-NULL
`cost_per_km_over_80`, the request must carry a destination city
(supplied via `destination_city=...` or parsed from the
`"{Country} - {City}"` lane display string), and the lane must have at
least one `airport_code_*`. Any failure surfaces a warning on the
result and leaves `intl_hotshot_surcharge = 0`.

**Final international total** = EQ-017 + EQ-018.

**Confirm-with-FSI threshold:** when `intl_hotshot_surcharge > 750`
USD, the workbook (`Z11`) flags the quote for manual confirmation. The
runtime surfaces this as `InternationalQuote.requires_confirmation = True`
plus a warning string.

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `notes` | str | — | `SCInternationalLane.notes` | `"Door to Door"` or `"Door to Airport"` |
| `rate_class` | str | — | `SCInternationalLane.rate_class` | `"Standard"` or `"Customer Specific"` |
| `cost_per_km_over_80` | float | USD/km | `SCInternationalLane.cost_per_km_over_80` | Per-km surcharge for distance beyond 80 km |
| `km_to_airport` | float | km | quote request (operator input today) | Distance from destination city to airport |

**Constraints:**

- Door-to-Airport lanes never apply the surcharge (the workbook's
  `R7 = "Door to Airport"` short-circuits `AA10`).
- Customer-Specific lanes never apply the surcharge (`R10 = "Customer
  Specific"` short-circuits `AA10`).
- A lane without a `cost_per_km_over_80` value never applies the
  surcharge (Ground / N/A lanes).
- `km_to_airport == 80` is below the threshold (workbook uses strict
  `> 80`).
- The runtime emits a warning when `km_to_airport` is omitted for a
  Door-to-Door Standard lane — the caller should refine the quote
  before delivering it to the customer.

**Code location:** `app/services/international_quote.py`,
`calculate_international_quote()` lines ~117-140.

**Worked example:** `Australia - Adelaide` lane for `SCAZ` (Door to Door,
`cost_per_km_over_80 = 1.25`):

    km_to_airport=130.4 -> round=130
    intl_hotshot_surcharge = (130 - 80) * 1.25 = 62.50 USD
    quote_total            = base + 62.50

    km_to_airport=780.0 -> round=780
    intl_hotshot_surcharge = (780 - 80) * 1.25 = 875.00 USD
    quote_total            = base + 875.00 (requires FSI confirmation)

**Last verified:** 2026-06-25
