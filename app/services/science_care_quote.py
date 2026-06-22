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
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from sqlalchemy import or_

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


def _auto_consumable_weight(
    temp_mode: str,
    scope: str,
    total_boxes: int,
    consumable_index: list[SCConsumable],
) -> float:
    """Sum the per-box consumable weight applicable to a leg automatically.

    Used as the fallback when the SC user submits a leg with every
    consumable Qty blank/zero. Pre-feature behaviour: pick every
    :class:`SCConsumable` row matching the leg's ``temp_mode`` + ``scope``
    (with ``"any"`` acting as a wildcard) and multiply its per-box
    weight by the leg's total box count.
    """

    if total_boxes <= 0:
        return 0.0
    temp = (temp_mode or "").strip().lower()
    scp = (scope or "").strip().lower()
    total = 0.0
    for row in consumable_index:
        row_temp = (row.temp_mode or "").lower()
        row_scope = (row.scope or "").lower()
        if row_temp not in (temp, "any"):
            continue
        if row_scope not in (scp, "any"):
            continue
        total += float(row.weight_lb_per_box or 0.0) * total_boxes
    return total


def _consumable_picks_from_form(
    form: Mapping[str, str],
    leg: int,
    consumable_index: list[SCConsumable],
) -> tuple[float, dict[int, int]]:
    """Read the user's per-consumable Qty inputs for one leg.

    Returns ``(total_weight, picks)``. ``picks`` is a dict mapping
    :class:`SCConsumable.id` to the qty the user typed (only non-zero
    entries appear). When every Qty for the leg is blank or zero the
    weight is zero and the caller falls back to :func:`_auto_consumable_weight`.
    """

    total = 0.0
    picks: dict[int, int] = {}
    for row in consumable_index:
        raw = form.get(f"cons_qty_{leg}_{row.id}")
        qty = _as_int(raw, default=0)
        if qty <= 0:
            continue
        picks[int(row.id)] = qty
        total += float(row.weight_lb_per_box or 0.0) * qty
    return total, picks


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

    Honours ``effective_from`` / ``effective_to`` so expired or
    not-yet-active rates don't leak into the rollup. A NULL bound is
    treated as open-ended (no lower or upper limit).
    """

    if not origin_zip or not dest_zip:
        return None
    today = date.today()
    rows = (
        SCEstablishedLane.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE,
            origin_zip=origin_zip,
            dest_zip=dest_zip,
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
        .all()
    )
    rates = [float(r.rate) for r in rows if r.rate is not None]
    return min(rates) if rates else None


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

        scope = "intl" if result.intl_country else "domestic"
        # Picks-first: trust the user's explicit Qty inputs when they
        # entered any. Otherwise fall back to the auto formula so SC
        # users who haven't started filling in the new fields still
        # get a reasonable consumable estimate.
        consumable_weight, picks = _consumable_picks_from_form(
            form, n, consumable_index
        )
        if not picks:
            consumable_weight = _auto_consumable_weight(
                result.temp_mode, scope, total_boxes, consumable_index
            )
        result.consumable_picks = picks
        total_weight += consumable_weight

        if total_weight <= 0:
            result.skip_reason = "computed weight is zero"
            legs.append(result)
            continue

        result.total_weight_lb = total_weight
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
        try:
            air_quote, _air_meta = create_quote(quote_type="Air", **common)
            result.air_quote = air_quote
        except Exception as exc:  # noqa: BLE001 - per-leg isolation
            logger.exception("Air quote failed for SC leg %d", n)
            result.error = f"Air quote failed: {exc}"

        try:
            hot_quote, _hot_meta = create_quote(
                quote_type="Hotshot", **common
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
    # submission verbatim.
    session = SCQuoteSession(
        user_id=getattr(user, "id", None),
        grand_total=grand_total,
        payload_json=json.dumps(dict(form)),
    )
    db.session.add(session)
    db.session.flush()
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
