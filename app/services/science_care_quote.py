"""Multi-leg Science Care quote orchestration.

Walks the form submitted by ``POST /sc/quote/calculate``, fires
:func:`app.services.quote.create_quote` twice per shipment leg (Air +
Hotshot), runs the SC-to-SC / cheapest-of-three rollup, and persists
the result as a :class:`~app.models.SCQuoteSession` plus one
:class:`~app.models.SCQuoteSessionLeg` per shipment.

This module reuses :func:`app.services.quote.create_quote` as the only
quoting entry point - the per-leg orchestrator never reimplements
freight pricing. Existing quote service code is not modified.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from sqlalchemy import Integer, cast, func, or_
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCAccessorialMap,
    SCBoxType,
    SCConsumable,
    SCEstablishedLane,
    SCLab,
    SCQuoteSession,
    SCQuoteSessionLeg,
    SCTissueBoxCapacity,
    SCTissueCode,
    db,
)
from app.services.constants import DIM_DIVISOR
from app.services.quote import create_quote
from app.services.zip_city_lookup import lookup_city_state


# Maximum number of shipment legs surfaced by the form. Mirrors the
# constant in :mod:`app.science_care.routes` so the orchestrator stays
# in lock-step with the rendered accordion.
SC_LEG_COUNT = 7

# Routing-mode literal that activates the established-lane override.
ROUTING_SC_TO_SC = "sc to sc"

# Quote.quote_source must fit in a db.String(20) column - keep this
# constant short or change models.py + a migration first.
QUOTE_SOURCE = "sc_multileg"

# SCQuoteSessionLeg.skip_reason maxes out at 60 characters. Long
# error messages from create_quote get truncated by _short_reason()
# before they hit the row so the final session commit doesn't fail
# with a value-too-long error.
_SKIP_REASON_MAX_LEN = 60


def _short_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = str(reason)
    if len(text) <= _SKIP_REASON_MAX_LEN:
        return text
    return text[: _SKIP_REASON_MAX_LEN - 1] + "…"


# --- data shapes -------------------------------------------------------------


@dataclass
class TissueRow:
    """One row of a leg's tissue table after form parsing.

    Attributes mirror the columns rendered by ``templates/sc/_tissue_row.html``.

    * ``user_box_code`` is the box-type code the user picked from the
      per-row dropdown (empty string when the user has not picked one
      yet). The allocator treats it as a hard override - even when the
      capacity table would recommend a different box.
    * ``box_type_code`` and ``tare_weight_lb`` are filled in during box
      allocation from the final resolved box (user pick > recommendation).
    * ``pieces`` defaults to 1 when the user leaves the qty blank for a
      code that prefilled.
    """

    tissue_code: str
    qty: int
    user_box_code: str = ""
    unit_weight_lb: float = 0.0
    box_type_code: str | None = None
    box_dims: tuple[float, float, float] | None = None
    tare_weight_lb: float = 0.0
    pieces_per_box: int | None = None


@dataclass
class LegResult:
    """Outcome of one shipment leg, persisted as :class:`SCQuoteSessionLeg`.

    Holds the Quote objects returned by :func:`create_quote` (or ``None``
    when the leg was skipped) plus the cheapest-of winner so the
    template can render a coherent row even when one quote_type failed.
    """

    leg_index: int
    lab_code: str = ""
    origin_zip: str = ""
    dest_zip: str = ""
    routing_type: str = ""
    temp_mode: str = ""
    intl_country: str = ""
    total_weight_lb: float = 0.0
    # Breakdown of total_weight_lb so the results card can show which
    # component (tissue payload vs. consumables vs. box tare) is driving
    # the leg's billable weight. Always sums to total_weight_lb.
    tissue_weight_lb: float = 0.0
    consumable_weight_lb: float = 0.0
    box_tare_weight_lb: float = 0.0
    total_boxes: int = 0
    dim_weight_lb: float = 0.0
    accessorial_labels: list[str] = field(default_factory=list)
    consumable_picks: dict[int, int] = field(default_factory=dict)
    box_counts: dict[str, int] = field(default_factory=dict)
    air_quote: Any | None = None
    hotshot_quote: Any | None = None
    established_rate: float | None = None
    winner_mode: str | None = None
    winner_total: float = 0.0
    skip_reason: str | None = None
    error: str | None = None


# --- helpers -----------------------------------------------------------------


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_zip(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text[:5].zfill(5) if text else ""


def _collect_tissue_rows(
    form: Mapping[str, str], leg: int
) -> list[TissueRow]:
    """Read every ``tissue_code_<leg>_<i>`` / ``qty_<leg>_<i>`` pair.

    Also picks up the per-row box override (``box_choice_<leg>_<i>``)
    when the user changed the dropdown - empty / missing means "use the
    recommended box for this tissue + qty".
    """

    rows: list[TissueRow] = []
    # Index is 1-based and may be sparse if the user removed rows in the
    # client; walk a generous range and stop after a long stretch of
    # missing entries.
    consecutive_blanks = 0
    i = 1
    while consecutive_blanks < 32:
        code = (form.get(f"tissue_code_{leg}_{i}") or "").strip().upper()
        qty = _as_int(form.get(f"qty_{leg}_{i}"), default=0)
        box_pick = (
            form.get(f"box_choice_{leg}_{i}") or ""
        ).strip().upper()
        if not code and qty == 0:
            consecutive_blanks += 1
            i += 1
            continue
        consecutive_blanks = 0
        if code:
            rows.append(
                TissueRow(
                    tissue_code=code,
                    qty=max(0, qty),
                    user_box_code=box_pick,
                )
            )
        i += 1
    return rows


def _tissue_box_capacity_index(
    tissue_codes: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Return ``{tissue_code: {box_code: pieces_per_box}}`` for the SC tenant.

    Passing ``tissue_codes`` filters the query to a subset (used by the
    live tissue-lookup endpoint so each keystroke does not scan the
    whole capacity table). Leaving it ``None`` loads everything for the
    multi-leg orchestrator's pre-cache.
    """

    query = SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    )
    if tissue_codes is not None:
        codes = sorted({c for c in tissue_codes if c})
        if not codes:
            return {}
        query = query.filter(SCTissueBoxCapacity.tissue_code.in_(codes))
    rows = query.all()
    index: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.pieces_per_box is None or row.pieces_per_box <= 0:
            continue
        index.setdefault(row.tissue_code, {})[row.box_code] = int(
            row.pieces_per_box
        )
    return index


def recommended_box_for_qty(
    qty: int,
    capacities: dict[str, int],
    box_index: dict[str, SCBoxType],
) -> tuple[str | None, int]:
    """Pick the box code that minimises the number of boxes for ``qty``.

    Ties on box count are broken by smaller interior volume - the
    customer's template was built around the same intuition (use the
    smallest box that still fits the items in one shot). Boxes whose
    dimensions are zero in ``box_index`` are skipped because the dim
    weight calculation would collapse to zero, which would silently
    undercount the freight.

    Returns ``(box_code, pieces_per_box)`` for the winning box, or
    ``(None, 0)`` when no capacity rows exist (the caller skips the
    leg with an "unknown capacity" error rather than guess).
    """

    if qty <= 0 or not capacities:
        return None, 0

    best: tuple[str, int] | None = None
    best_box_count = 0
    best_volume = 0.0

    for box_code, per_box in capacities.items():
        if per_box <= 0:
            continue
        box = box_index.get(box_code)
        if box is None:
            continue
        volume = (
            float(box.length_in or 0.0)
            * float(box.width_in or 0.0)
            * float(box.height_in or 0.0)
        )
        # Zero-dimension boxes (SMALL_AIRTRAY placeholder) are not
        # quote-able yet - the dim-weight contribution would be zero.
        if volume <= 0.0:
            continue
        boxes_needed = math.ceil(qty / per_box)
        if (
            best is None
            or boxes_needed < best_box_count
            or (boxes_needed == best_box_count and volume < best_volume)
        ):
            best = (box_code, per_box)
            best_box_count = boxes_needed
            best_volume = volume

    if best is None:
        return None, 0
    return best


def _finalize_box_totals(
    boxes_by_type: dict[str, int],
    tissue_only_weight: float,
    box_index: dict[str, SCBoxType],
) -> tuple[float, int, float]:
    """Apply tare + dim weight to a ``boxes_by_type`` dict.

    Pulled out of :func:`allocate_boxes` so the override path can reuse
    the same arithmetic without re-walking tissue rows. Given a starting
    tissue-only weight, it adds each box's tare × count and computes the
    dim weight as ``Σ L×W×H×count / DIM_DIVISOR``. Box codes not present
    in ``box_index`` are silently dropped (same forgiving behaviour as
    today's allocator).
    """

    total_weight = tissue_only_weight
    dim_weight = 0.0
    total_boxes = 0
    for code, count in boxes_by_type.items():
        box = box_index.get(code)
        if box is None:
            # Skip unknown box codes for the count too - otherwise
            # total_boxes would be inflated by boxes whose tare and
            # dims never landed in total_weight/dim_weight.
            continue
        total_boxes += count
        total_weight += float(box.tare_weight_lb or 0.0) * count
        dim_weight += (
            float(box.length_in or 0.0)
            * float(box.width_in or 0.0)
            * float(box.height_in or 0.0)
            * count
        ) / DIM_DIVISOR
    return total_weight, total_boxes, dim_weight


def allocate_boxes(
    tissue_rows: list[TissueRow],
    tissue_index: dict[str, SCTissueCode],
    box_index: dict[str, SCBoxType],
    capacity_index: dict[str, dict[str, int]] | None = None,
    box_overrides: dict[str, int] | None = None,
) -> tuple[float, int, dict[str, int], float, list[str]]:
    """Resolve tissue rows into total weight, box counts, and dim weight.

    Returns:
        ``(total_weight_lb, total_boxes, boxes_by_type, dim_weight_lb,
        unknown_codes)``.

    * ``total_weight_lb`` is the sum of (qty × unit weight) for every
      tissue plus the tare weight of every allocated box (consumables
      are added by the caller).
    * ``boxes_by_type`` maps ``SCBoxType.code`` → integer box count and
      seeds the consumable lookup and the rollup display.
    * ``dim_weight_lb`` is the sum across box-type groups of
      ``L × W × H × count / DIM_DIVISOR``.
    * ``unknown_codes`` lists tissue codes the user submitted that are
      not in ``tissue_index``. The caller skips the whole leg when this
      list is non-empty so a single typo can't undercount the freight.

    Per-tissue box choice is resolved in this order:

    1. ``row.user_box_code`` if the user picked a box from the dropdown
       AND the capacity table allows that pairing.
    2. The recommended box from :func:`recommended_box_for_qty` (smallest
       box count for ``row.qty``, ties → smaller interior volume).
    3. The legacy ``default_box_type_code`` on :class:`SCTissueCode` when
       no capacity rows exist yet (lets tenants who haven't loaded the
       expanded CSV still get a quote).

    ``box_overrides`` (optional) lets the caller replace the auto box
    allocation entirely. When supplied with at least one non-zero entry,
    the tissue weight calculation is unchanged but the box counts (and
    therefore the tare + dim weight) come from ``box_overrides``
    verbatim. Unknown box codes are silently dropped via
    :func:`_finalize_box_totals`.
    """

    if capacity_index is None:
        capacity_index = {}

    tissue_only_weight = 0.0
    auto_boxes: dict[str, int] = {}
    unknown_codes: list[str] = []
    for row in tissue_rows:
        tissue = tissue_index.get(row.tissue_code)
        if tissue is None:
            unknown_codes.append(row.tissue_code)
            continue
        row.unit_weight_lb = float(tissue.unit_weight_lb or 0.0)
        if row.qty <= 0:
            continue
        tissue_only_weight += row.unit_weight_lb * row.qty

        capacities = capacity_index.get(row.tissue_code, {})
        chosen_box: str | None = None
        chosen_per_box: int = 0
        user_pick = (row.user_box_code or "").strip().upper()
        if user_pick and capacities.get(user_pick, 0) > 0:
            chosen_box = user_pick
            chosen_per_box = capacities[user_pick]
        elif capacities:
            chosen_box, chosen_per_box = recommended_box_for_qty(
                row.qty, capacities, box_index
            )

        # Legacy fall-through: tenants whose CSV upload predates the
        # capacity table still have default_box_type_code populated on
        # SCTissueCode. Use it so they keep getting quotes.
        if chosen_box is None and tissue.default_box_type_code:
            chosen_box = tissue.default_box_type_code
            chosen_per_box = max(1, int(tissue.pieces_per_box or 1))

        row.box_type_code = chosen_box or ""
        row.pieces_per_box = chosen_per_box or None
        if not chosen_box:
            continue
        per_box = max(1, chosen_per_box)
        box_count = math.ceil(row.qty / per_box)
        auto_boxes[chosen_box] = auto_boxes.get(chosen_box, 0) + box_count
        box = box_index.get(chosen_box)
        if box is not None:
            row.tare_weight_lb = float(box.tare_weight_lb or 0.0)
            row.box_dims = (
                float(box.length_in or 0.0),
                float(box.width_in or 0.0),
                float(box.height_in or 0.0),
            )

    # Apply overrides (if any non-zero entries) - they replace the auto
    # allocation entirely so the user can intentionally consolidate
    # multiple tissues into one box or split into more.
    if box_overrides:
        boxes_by_type = {
            code: count for code, count in box_overrides.items() if count > 0
        }
    else:
        boxes_by_type = auto_boxes

    total_weight, total_boxes, dim_weight = _finalize_box_totals(
        boxes_by_type, tissue_only_weight, box_index
    )
    return total_weight, total_boxes, boxes_by_type, dim_weight, unknown_codes


def _default_consumable_for_mode(
    temp_mode: str,
    consumable_index: list[SCConsumable],
) -> SCConsumable | None:
    """Return the consumable that gets auto-applied for ``temp_mode``.

    Business rule: a leg with no user-entered consumable quantity still
    needs the standard temperature packaging budget. ``frozen`` legs
    default to one **domestic dry ice** unit per box; ``rtu`` (ready to
    use) legs default to one **domestic gel pack** per box. The match
    is case-insensitive on ``consumable_type`` and ``scope`` so a CSV
    upload that titlecases ``Dry_Ice`` doesn't bypass the default.

    Returns ``None`` when no matching ``SCConsumable`` row is
    configured for the tenant - the orchestrator silently skips the
    default in that case so a freshly-migrated SC tenant doesn't crash
    waiting on reference data.
    """

    tm = (temp_mode or "").strip().lower()
    if tm == "frozen":
        target_type = "dry_ice"
    elif tm in {"rtu", "ready_to_use", "ready to use"}:
        target_type = "gel_pack"
    else:
        return None
    for cons in consumable_index:
        if (
            (cons.consumable_type or "").strip().lower() == target_type
            and (cons.scope or "").strip().lower() == "domestic"
        ):
            return cons
    return None


def _consumable_picks_from_form(
    form: Mapping[str, str],
    leg: int,
    consumable_index: list[SCConsumable],
    *,
    total_boxes: int = 0,
    temp_mode: str = "",
) -> tuple[float, dict[int, int]]:
    """Read the user's per-consumable Qty inputs for one leg.

    Returns ``(total_weight, picks)``. ``picks`` is a dict mapping
    :class:`SCConsumable.id` to the qty the user typed (only non-zero
    entries appear).

    Semantics — matches the "prefill blank only" rule used by
    box-count overrides:

    * Any non-blank typed value wins (including a deliberate ``0``,
      which means "no units of this consumable").
    * A truly blank input falls back to the temp_mode default - one
      domestic dry ice per box for ``frozen``, one domestic gel pack
      per box for ``rtu`` - applied to the matching consumable row
      only. Non-matching rows stay at 0 unless the user types.
    * The default needs ``total_boxes`` and ``temp_mode``; callers
      that don't pass them get the legacy "no auto-default" behaviour.
    """

    auto_default = _default_consumable_for_mode(
        temp_mode, consumable_index
    )
    # SCConsumable.id is already an int after commit; no cast needed.
    # Skipping the int() also avoids a TypeError on transient or mocked
    # rows whose id hasn't been assigned yet.
    default_id = auto_default.id if auto_default is not None else None

    total = 0.0
    picks: dict[int, int] = {}
    for row in consumable_index:
        raw = form.get(f"cons_qty_{leg}_{row.id}")
        if raw is None or str(raw).strip() == "":
            # Blank input: auto-apply the temp_mode default ONLY for
            # the matching consumable row. Anything else stays at 0.
            if row.id == default_id and total_boxes > 0:
                qty = total_boxes
            else:
                qty = 0
        else:
            qty = _as_int(raw, default=0)
        if qty <= 0:
            continue
        picks[row.id] = qty
        total += float(row.weight_lb_per_box or 0.0) * qty
    return total, picks


def compute_leg_subtotals(
    form: Mapping[str, str],
    leg: int,
    tissue_rows: list[TissueRow],
    boxes_by_type: dict[str, int],
    box_index: dict[str, SCBoxType],
    consumable_index: list[SCConsumable],
) -> dict[str, float]:
    """Compute tissue / consumable / box-tare / total subtotals for one leg.

    Returns a dict with ``tissue_lb``, ``consumable_lb``, ``box_tare_lb``,
    and ``total_lb`` keys. Used by the live HTMX endpoints to keep the
    per-leg weight subtotals card in sync with whatever the user just
    changed (qty, box override, consumable Qty, temp_mode, etc.).

    ``tissue_rows`` must already be populated by :func:`allocate_boxes` so
    each row carries the correct ``unit_weight_lb``; ``boxes_by_type`` is
    the allocator's final per-box-code count.
    """

    tissue_lb = sum(r.unit_weight_lb * r.qty for r in tissue_rows)
    box_tare_lb = sum(
        float(box_index[code].tare_weight_lb or 0.0) * count
        for code, count in boxes_by_type.items()
        if code in box_index
    )

    # Explicit user picks win (including a typed 0). A blank input on
    # the temp_mode-matching consumable falls back to ``1 per box`` so
    # the live subtotal card mirrors what the orchestrator will use.
    total_boxes = int(sum(boxes_by_type.values()))
    temp_mode = (form.get(f"temp_mode_{leg}") or "").strip()
    consumable_lb, _picks = _consumable_picks_from_form(
        form,
        leg,
        consumable_index,
        total_boxes=total_boxes,
        temp_mode=temp_mode,
    )

    total_lb = tissue_lb + box_tare_lb + consumable_lb
    return {
        "tissue_lb": tissue_lb,
        "consumable_lb": consumable_lb,
        "box_tare_lb": box_tare_lb,
        "total_lb": total_lb,
    }


def _box_overrides_from_form(
    form: Mapping[str, str],
    leg: int,
    box_index: dict[str, SCBoxType],
) -> dict[str, int]:
    """Read the user's per-box-type Count inputs for one leg.

    Returns ``{box_code: count}`` for non-zero entries (empty dict when
    every input is blank or zero). The form keys are
    ``box_count_<leg>_<box_id>`` - we resolve the ID back to the box
    code via ``box_index`` so the rest of the pipeline keeps speaking
    string codes.
    """

    overrides: dict[str, int] = {}
    for box in box_index.values():
        # Skip uncommitted / mock objects whose id hasn't been
        # populated yet - int(None) in the form-key f-string would
        # raise TypeError. In production every box_index value comes
        # from a committed row so this is just a defensive guard for
        # unit-test stubs.
        if box.id is None:
            continue
        raw = form.get(f"box_count_{leg}_{box.id}")
        count = _as_int(raw, default=0)
        if count <= 0:
            continue
        overrides[box.code] = count
    return overrides


def _collect_accessorials(
    form: Mapping[str, str],
    leg: int,
    accessorial_map: dict[str, SCAccessorialMap],
) -> list[str]:
    """Return the list of accessorial *names* to send to ``create_quote``.

    Maps each checked form field (``acc_J3_<n>``, ``acc_J7_<n>`` etc.)
    through :class:`SCAccessorialMap.accessorial_name`. Unknown form
    fields are skipped.
    """

    labels: list[str] = []
    prefix = "acc_"
    suffix = f"_{leg}"
    for key, value in form.items():
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        if str(value).strip().upper() not in {"Y", "ON", "TRUE", "1"}:
            continue
        # acc_<form_field>_<leg> -> strip both ends to recover form_field.
        form_field = key[len(prefix) : -len(suffix)]
        if not form_field:
            continue
        mapping = accessorial_map.get(form_field)
        if mapping and mapping.accessorial_name:
            labels.append(mapping.accessorial_name)
    return labels


def _cheapest_for_leg(
    routing_type: str,
    air_total: float,
    hotshot_total: float,
    established_rate: float | None,
) -> tuple[str | None, float]:
    """Return ``(winner_mode, winner_total)`` for one leg.

    Mirrors the VBA ``CheapestFreight`` / ``IsScToSc`` logic that drives
    the SHIPMENT 1 ``A44:C51`` summary in the SC workbook:

    * When ``routing_type`` is ``"SC to SC"`` AND an established lane
      exists, use the established rate (pre-negotiated lab-to-lab).
    * Otherwise pick the cheapest non-zero of
      ``{Air, Hotshot, Established Lane}``.
    * Falls back to ``min(Air, Hotshot)`` when SC-to-SC is selected but
      no established rate is configured, so the leg still contributes a
      freight cost instead of zeroing out.
    """

    candidates: list[tuple[str, float]] = []
    if air_total and air_total > 0:
        candidates.append(("Air", air_total))
    if hotshot_total and hotshot_total > 0:
        candidates.append(("Hotshot", hotshot_total))
    if established_rate and established_rate > 0:
        candidates.append(("Established", float(established_rate)))

    is_sc_to_sc = (routing_type or "").strip().lower() == ROUTING_SC_TO_SC
    if is_sc_to_sc and established_rate and established_rate > 0:
        return "Established", float(established_rate)

    if not candidates:
        return None, 0.0
    winner_mode, winner_total = min(candidates, key=lambda item: item[1])
    return winner_mode, winner_total


def _lookup_established(
    origin_zip: str, dest_zip: str
) -> float | None:
    """Return the lowest Established Lane rate that covers a leg today.

    Resolution order, mirroring the workbook's behaviour:

    1. Exact ``(origin_zip, dest_zip)`` match.
    2. If no ZIP match, resolve ``dest_zip`` to its ``(city, state)``
       via ``Zipcode_Zones.csv`` and match any lane row for the same
       ``origin_zip`` whose ``dest_city`` / ``dest_state`` align. This
       mirrors the spreadsheet's ``lab_code + "City,State"`` VLOOKUP -
       a lane for "Mahwah, NJ" applies to any Mahwah ZIP, not just the
       one representative ZIP seeded into ``dest_zip``.

    Honours ``effective_from`` / ``effective_to`` so expired or
    not-yet-active rates don't leak into the rollup. A NULL bound is
    treated as open-ended (no lower or upper limit). The lowest active
    rate across whichever match path produced rows is returned.
    """

    if not origin_zip or not dest_zip:
        return None
    today = date.today()
    active = (
        SCEstablishedLane.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE,
            origin_zip=origin_zip,
        )
        .filter(SCEstablishedLane.service_type.in_(["Air", "Hotshot", "Any"]))
        .filter(
            or_(
                SCEstablishedLane.effective_from.is_(None),
                SCEstablishedLane.effective_from <= today,
            ),
            or_(
                SCEstablishedLane.effective_to.is_(None),
                SCEstablishedLane.effective_to >= today,
            ),
        )
    )

    zip_rows = active.filter(SCEstablishedLane.dest_zip == dest_zip).all()
    rates = [float(r.rate) for r in zip_rows if r.rate is not None]
    if rates:
        return min(rates)

    # ZIP miss: fall back to metro match. lookup_city_state returns
    # uppercase; pushing the uppercase compare into SQL via db.func.upper
    # keeps the row transfer small and avoids a per-request Python scan
    # over every lane row for the origin.
    city_state = lookup_city_state(dest_zip)
    if city_state is None:
        return None
    city, state = city_state
    metro_rows = active.filter(
        db.func.upper(SCEstablishedLane.dest_city) == city,
        db.func.upper(SCEstablishedLane.dest_state) == state,
    ).all()
    metro_rates = [float(r.rate) for r in metro_rows if r.rate is not None]
    return min(metro_rates) if metro_rates else None


# --- multi-leg reference number ---------------------------------------------

# Prefix and zero-padded width for the auto-assigned reference. Width
# grows beyond 9999 (``SCMQ10000``); 4 is the floor, not a hard cap.
SCMQ_PREFIX = "SCMQ"
SCMQ_PAD = 4
# Cap the insert-retry loop so a wedged UNIQUE-constraint situation
# surfaces as an error instead of looping forever.
_SCMQ_MAX_ATTEMPTS = 8

# ``Quote.client_reference`` is ``String(64)`` (see app/models.py). The
# multi-leg orchestrator stamps ``{multi_reference}-L{n}-{AIR|HOT}`` on
# each leg, so the suffix grows with the leg count. Cap the base
# reference so even a max-length value still fits the per-leg column
# after the worst-case suffix is appended; otherwise the leg's
# create_quote() would raise a DataError that the orchestrator's
# per-leg ``except`` handler would treat as a quote failure and the
# user would see legs silently disappear. Derived from SC_LEG_COUNT so
# growing the leg count automatically narrows the multi-reference
# cap without manual sync.
_MAX_LEG_SUFFIX_LEN = len(f"-L{SC_LEG_COUNT}-HOT")
MAX_MULTI_REFERENCE_LENGTH = 64 - _MAX_LEG_SUFFIX_LEN


def _next_scmq_number() -> int:
    """Return ``MAX(numeric suffix) + 1`` across existing SCMQ refs.

    Reads the highest auto-assigned ``SCMQ\\d+`` number directly via SQL
    so the per-submit hot path doesn't have to transfer every prior
    reference over the wire. The ``~`` regex filter is PostgreSQL-only,
    matching the project's deployment target (Cloud SQL Postgres) and
    its test DSN. Customer-supplied references that don't fit the
    pattern are excluded by the regex filter.

    Returns ``1`` when no auto-assigned references exist yet.
    """

    # SUBSTRING is 1-indexed in Postgres; offset past the literal prefix
    # to isolate the numeric tail, then CAST to integer so MAX picks the
    # numeric maximum rather than a lexicographic one (``SCMQ9`` would
    # otherwise sort after ``SCMQ10``).
    numeric_part = cast(
        func.substring(
            SCQuoteSession.multi_reference, len(SCMQ_PREFIX) + 1
        ),
        Integer,
    )
    highest = (
        db.session.query(func.max(numeric_part))
        .filter(
            SCQuoteSession.multi_reference.isnot(None),
            SCQuoteSession.multi_reference.op("~")(
                f"^{SCMQ_PREFIX}[0-9]+$"
            ),
        )
        .scalar()
    )
    return int(highest or 0) + 1


def _format_scmq(number: int) -> str:
    """Format ``number`` as a zero-padded ``SCMQNNNN`` reference."""

    return f"{SCMQ_PREFIX}{number:0{SCMQ_PAD}d}"


def _normalize_multi_reference(raw: Any) -> tuple[str | None, str | None]:
    """Trim + uppercase a customer-supplied multi reference.

    Mirrors :func:`app.quotes.routes._normalize_client_reference` so a
    user-provided value is normalised the same way whether it's typed
    into the single-quote form or the SC multi-leg form. Returns
    ``(normalized, error_message)``; ``normalized`` is ``None`` when the
    field was left blank (caller should auto-assign).
    """

    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "Reference must be a string."
    normalized = " ".join(raw.strip().upper().split())
    if not normalized:
        return None, None
    # The cap is shorter than the 64-char ``Quote.client_reference``
    # column because we suffix each leg with up to 7 characters
    # (``-L7-HOT``). See MAX_MULTI_REFERENCE_LENGTH.
    if len(normalized) > MAX_MULTI_REFERENCE_LENGTH:
        return (
            None,
            f"Reference must be {MAX_MULTI_REFERENCE_LENGTH} characters "
            "or fewer.",
        )
    # Same character class as the single-quote form so a value typed
    # here can later be looked up via /quotes lookup without surprise.
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-_/ ]*", normalized):
        return (
            None,
            "Reference may contain letters, numbers, spaces, dashes, "
            "underscores, or forward slashes.",
        )
    return normalized, None


# --- public entry point ------------------------------------------------------


def compute_sc_multileg(
    form: Mapping[str, str], user, request_ip: str | None
) -> dict:
    """Run the multi-leg orchestration end-to-end.

    Args:
        form: The submitted ``POST /sc/quote/calculate`` payload.
            Accepts any ``Mapping[str, str]``-like view (Flask's
            ``request.form`` works as-is).
        user: The currently authenticated :class:`app.models.User`.
        request_ip: Caller IP for audit, forwarded to ``create_quote``.

    Returns:
        A dict ready for ``templates/sc/_results_partial.html`` with the
        per-leg :class:`LegResult` rows, the grand total, and the
        persisted :class:`SCQuoteSession`.
    """

    # Pre-cache the four reference dimensions we need so the per-leg
    # loop does not hit the database for every tissue / box / consumable
    # lookup.
    tissue_index = {
        t.tissue_code: t
        for t in SCTissueCode.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    box_index = {
        b.code: b
        for b in SCBoxType.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    capacity_index = _tissue_box_capacity_index()
    consumable_index = SCConsumable.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).all()
    accessorial_map = {
        a.form_field: a
        for a in SCAccessorialMap.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    lab_index = {
        lab.lab_code: lab
        for lab in SCLab.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE, is_active=True
        ).all()
    }

    # Resolve the unified reference up-front so each per-leg Quote row
    # can stamp it. Customer-supplied wins; blank falls back to the
    # next ``SCMQNNNN``. Validation errors get raised so the route can
    # flash them - the form-level handler is on the route, not here.
    customer_ref, ref_error = _normalize_multi_reference(
        form.get("multi_reference")
    )
    if ref_error:
        raise ValueError(ref_error)
    # If the customer reused an existing reference (intentional, e.g.
    # "this is leg 8 of an in-flight job"), we reject it before any
    # ``create_quote`` side-effects fire. UNIQUE on the session would
    # surface the same error later, but only after up to 14 Quote rows
    # had already been committed.
    if customer_ref is not None and (
        db.session.query(SCQuoteSession.id)
        .filter(SCQuoteSession.multi_reference == customer_ref)
        .first()
        is not None
    ):
        raise ValueError(
            f"Reference {customer_ref!r} is already in use."
        )
    # Reserve a candidate auto-number now so each leg's Quote row can
    # stamp it. The final commit re-checks UNIQUE and retries with the
    # next number on collision (concurrent multi-leg submits).
    multi_reference = customer_ref or _format_scmq(_next_scmq_number())

    legs: list[LegResult] = []
    grand_total = 0.0

    for n in range(1, SC_LEG_COUNT + 1):
        result = LegResult(leg_index=n)
        result.lab_code = (form.get(f"lab_code_{n}") or "").strip().upper()
        result.dest_zip = _normalize_zip(form.get(f"dest_zip_{n}"))
        result.routing_type = (
            form.get(f"routing_type_{n}") or ""
        ).strip()
        result.temp_mode = (form.get(f"temp_mode_{n}") or "").strip()
        result.intl_country = (
            form.get(f"intl_country_{n}") or ""
        ).strip()

        # International guard - record and continue without touching the
        # quote service. The leg contributes $0 to the grand total.
        if result.intl_country:
            result.skip_reason = "international"
            legs.append(result)
            continue

        if not result.lab_code or result.lab_code not in lab_index:
            result.skip_reason = "missing or unknown lab code"
            legs.append(result)
            continue
        result.origin_zip = _normalize_zip(
            lab_index[result.lab_code].origin_zip
        )
        if len(result.origin_zip) != 5:
            result.skip_reason = "lab has no valid origin ZIP"
            legs.append(result)
            continue
        if len(result.dest_zip) != 5:
            result.skip_reason = "destination ZIP empty or invalid"
            legs.append(result)
            continue

        tissue_rows = _collect_tissue_rows(form, n)
        if not tissue_rows:
            result.skip_reason = "no tissue rows"
            legs.append(result)
            continue

        # Picks-first: when the user typed any box-count override for
        # the leg, allocate_boxes uses those instead of the auto
        # allocation from tissue rows. Blank submit -> falls back to
        # today's tissue-driven allocation.
        box_overrides = _box_overrides_from_form(form, n, box_index)
        (
            total_weight,
            total_boxes,
            boxes_by_type,
            dim_weight,
            unknown_codes,
        ) = allocate_boxes(
            tissue_rows,
            tissue_index,
            box_index,
            capacity_index=capacity_index,
            box_overrides=box_overrides,
        )
        result.box_counts = dict(boxes_by_type)
        if unknown_codes:
            # Refuse the leg rather than silently undercount weight on
            # what may be a typo. The form already shows unknown codes
            # with Bootstrap's invalid styling so the user has a clear
            # cue where to look.
            result.skip_reason = (
                "unknown tissue code(s): "
                + ", ".join(sorted(set(unknown_codes))[:4])
            )
            legs.append(result)
            continue

        # Consumables: explicit user values win (including 0); blanks
        # fall back to the temp_mode default - 1 domestic dry_ice per
        # box for frozen, 1 domestic gel_pack per box for rtu. See
        # _default_consumable_for_mode().
        consumable_weight, picks = _consumable_picks_from_form(
            form,
            n,
            consumable_index,
            total_boxes=total_boxes,
            temp_mode=result.temp_mode,
        )
        result.consumable_picks = picks
        total_weight += consumable_weight

        if total_weight <= 0:
            result.skip_reason = "computed weight is zero"
            legs.append(result)
            continue

        # Split total_weight back into its three contributing buckets so
        # the results card can render a breakdown. allocate_boxes returns
        # tissue+tare folded into total_weight - re-derive each piece
        # from the same primitives (tissue qty × unit_weight; box tare ×
        # count) so the three numbers sum to total_weight_lb exactly.
        # `unit_weight_lb` is already resolved + float-cast on every row
        # by allocate_boxes; the box tare is float-cast here to match
        # _finalize_box_totals's behaviour with Decimal-typed columns.
        tissue_weight = sum(r.unit_weight_lb * r.qty for r in tissue_rows)
        box_tare_weight = sum(
            float(box_index[code].tare_weight_lb or 0.0) * count
            for code, count in boxes_by_type.items()
            if code in box_index
        )

        result.total_weight_lb = total_weight
        result.tissue_weight_lb = tissue_weight
        result.consumable_weight_lb = consumable_weight
        result.box_tare_weight_lb = box_tare_weight
        result.total_boxes = max(total_boxes, 1)
        result.dim_weight_lb = dim_weight
        result.accessorial_labels = _collect_accessorials(
            form, n, accessorial_map
        )

        common = dict(
            user_id=getattr(user, "id", None),
            user_email=getattr(user, "email", None),
            origin=result.origin_zip,
            destination=result.dest_zip,
            weight=total_weight,
            pieces=result.total_boxes,
            dim_weight=dim_weight,
            accessorials=result.accessorial_labels,
            rate_set=RATE_SET_SCIENCE_CARE,
            quote_source=QUOTE_SOURCE,
            request_ip=request_ip,
        )

        # NOTE: do NOT call db.session.rollback() in these except handlers.
        # create_quote() manages its own session and commits internally, so
        # a rollback here would only affect db.session - which already
        # holds the pre-cached SCTissueCode / SCBoxType / SCConsumable /
        # SCAccessorialMap / SCLab instances. Rolling back would expire
        # every one of them, forcing N+1 SELECTs on the next iteration.
        # Stamp each underlying Quote row with the multi-reference plus
        # a per-leg / per-mode suffix so the per-user UNIQUE constraint
        # on (user_id, client_reference) doesn't collide across the
        # leg's two Quote rows or across legs of the same session.
        try:
            air_quote, _air_meta = create_quote(
                quote_type="Air",
                client_reference=f"{multi_reference}-L{n}-AIR",
                **common,
            )
            result.air_quote = air_quote
        except Exception as exc:  # noqa: BLE001 - per-leg isolation
            logger.exception("Air quote failed for SC leg %d", n)
            result.error = f"Air quote failed: {exc}"

        try:
            hot_quote, _hot_meta = create_quote(
                quote_type="Hotshot",
                client_reference=f"{multi_reference}-L{n}-HOT",
                **common,
            )
            result.hotshot_quote = hot_quote
        except Exception as exc:  # noqa: BLE001
            logger.exception("Hotshot quote failed for SC leg %d", n)
            err = f"Hotshot quote failed: {exc}"
            result.error = f"{result.error}; {err}" if result.error else err

        result.established_rate = _lookup_established(
            result.origin_zip, result.dest_zip
        )

        air_total = (
            float(result.air_quote.total)
            if result.air_quote is not None
            and result.air_quote.total is not None
            else 0.0
        )
        hotshot_total = (
            float(result.hotshot_quote.total)
            if result.hotshot_quote is not None
            and result.hotshot_quote.total is not None
            else 0.0
        )
        winner_mode, winner_total = _cheapest_for_leg(
            result.routing_type,
            air_total,
            hotshot_total,
            result.established_rate,
        )
        result.winner_mode = winner_mode
        result.winner_total = winner_total
        grand_total += winner_total
        legs.append(result)

    # Persist the multi-leg session + the seven leg rows so a future
    # "view past quote" page (out of scope here) can re-render the
    # submission verbatim. The auto-assigned ``multi_reference`` may
    # collide with a concurrent multi-leg submit that grabbed the same
    # number between our SELECT and INSERT; catch the IntegrityError,
    # bump to the next free number, and retry. Customer-supplied refs
    # don't retry - the customer picked the value, so a collision is a
    # user error and bubbles up.
    session = SCQuoteSession(
        user_id=getattr(user, "id", None),
        grand_total=grand_total,
        payload_json=json.dumps(dict(form)),
        multi_reference=multi_reference,
    )
    db.session.add(session)
    for _ in range(_SCMQ_MAX_ATTEMPTS):
        try:
            db.session.flush()
            break
        except IntegrityError:
            db.session.rollback()
            if customer_ref is not None:
                # The customer typed a value that survived our pre-check
                # but lost a race with a concurrent submit. Surface as a
                # ValueError so the route's existing error path flashes
                # a message instead of 500ing.
                raise ValueError(
                    f"Reference {customer_ref!r} is already in use."
                )
            # The Quote rows already committed in create_quote() carry
            # the stale ``<old_ref>-L<n>-<MODE>`` client_reference. If we
            # left them alone, a customer looking up the leg by the
            # session's final reference would find nothing - and a
            # lookup of the stale string would surface a Quote that no
            # longer belongs to a session with that ref. Re-stamp them
            # to the new reference via merge() so the per-leg lookup
            # stays consistent with SCQuoteSession.multi_reference.
            new_multi_reference = _format_scmq(_next_scmq_number())
            for leg_result in legs:
                if leg_result.air_quote is not None:
                    leg_result.air_quote.client_reference = (
                        f"{new_multi_reference}-L{leg_result.leg_index}-AIR"
                    )
                    db.session.merge(leg_result.air_quote)
                if leg_result.hotshot_quote is not None:
                    leg_result.hotshot_quote.client_reference = (
                        f"{new_multi_reference}-L{leg_result.leg_index}-HOT"
                    )
                    db.session.merge(leg_result.hotshot_quote)
            multi_reference = new_multi_reference
            session = SCQuoteSession(
                user_id=getattr(user, "id", None),
                grand_total=grand_total,
                payload_json=json.dumps(dict(form)),
                multi_reference=multi_reference,
            )
            db.session.add(session)
    else:
        raise RuntimeError(
            f"Could not assign a unique multi_reference after "
            f"{_SCMQ_MAX_ATTEMPTS} attempts."
        )
    for result in legs:
        db.session.add(
            SCQuoteSessionLeg(
                session_id=session.id,
                leg_index=result.leg_index,
                air_quote_id=getattr(result.air_quote, "id", None),
                hotshot_quote_id=getattr(result.hotshot_quote, "id", None),
                established_rate=result.established_rate,
                winner_mode=result.winner_mode
                or ("Skipped" if result.skip_reason else None),
                winner_total=result.winner_total,
                skip_reason=_short_reason(
                    result.skip_reason or result.error
                ),
                consumables_json=(
                    json.dumps(result.consumable_picks)
                    if result.consumable_picks
                    else None
                ),
                boxes_json=(
                    json.dumps(result.box_counts)
                    if result.box_counts
                    else None
                ),
            )
        )
    db.session.commit()

    return {
        "session": session,
        "legs": legs,
        "grand_total": grand_total,
        "skipped": [r for r in legs if r.skip_reason],
    }
