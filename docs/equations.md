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

## EQ-003: Science Care Leg Total Weight

**Purpose:** Compute the billable weight contribution of one shipment leg.

**Formula:**

    leg_weight_lb = Σ_tissue ( qty × unit_weight ) +
                    Σ_box ( count_box × tare_weight ) +
                    consumable_weight

    consumable_weight = (Σ_picked_row weight_per_box × user_qty)  if user picked any
                      = Σ_matching_consumable weight_per_box × total_boxes  otherwise

**Variables:**

| Variable | Type | Unit | Source | Description |
| --- | --- | --- | --- | --- |
| `qty` | int | count | Tissue row form input | Pieces of this tissue |
| `unit_weight` | float | lb | `SCTissueCode.unit_weight_lb` | Per-piece avg weight |
| `count_box` | int | count | Auto-allocator or override | Boxes of this size on the leg |
| `tare_weight` | float | lb | `SCBoxType.tare_weight_lb` | Empty-box weight |
| `weight_per_box` | float | lb | `SCConsumable.weight_lb_per_box` | Dry ice / gel pack added per box |
| `user_qty` | int | count | Form field `cons_qty_<leg>_<id>` | Per-consumable user override |
| `total_boxes` | int | count | Sum of `count_box` across all boxes | Used by the auto-consumable fallback |

**Constraints:**

- Consumable rows are filtered by `temp_mode` (frozen / rtu) and `scope`
  (domestic / intl); `"any"` acts as a wildcard.
- A leg with `leg_weight_lb <= 0` is skipped (no quote attempted).
- Box overrides at the leg level (`box_count_<leg>_<box_id>`) replace the
  auto-allocator's per-tissue boxes; the tissue weight does NOT change, only
  the tare + dim totals.

**Code location:** `app/services/science_care_quote.py`,
`_finalize_box_totals()` (~line 175) and `compute_sc_multileg()` (~line 600).

**Worked example:** Leg with one `PELV03` (79 lb) shipping in 1 X-Large box
(tare 14 lb) at `frozen` / `domestic`, no user-picked consumables:

    tissue_weight     = 1 × 79 = 79 lb
    box_tare_weight   = 1 × 14 = 14 lb
    auto_consumables  = 1 box × 25 lb (dry ice frozen / domestic) = 25 lb
    leg_weight_lb     = 79 + 14 + 25 = 118 lb

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

**Code location:** `app/services/science_care_quote.py`,
`_cheapest_for_leg()` (~line 480) and `_lookup_established()` (~line 519).

**Worked example:** Outbound leg, Air = $300, Hotshot = $250, Established
= $200:

    candidates = [Air 300, Hotshot 250, Established 200]
    winner = ("Established", 200)

Same totals but `routing_type = "SC to SC"`:

    winner = ("Established", 200)  # SC to SC always uses established when available

`routing_type = "SC to SC"` and `Established = None`:

    winner = ("Hotshot", 250)  # fall through to cheapest-of

**Last verified:** 2026-06-22
