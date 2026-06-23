"""Routes for the Science Care multi-lab quote page.

This module exposes:

* ``GET /sc/quote`` — renders the seven-leg multi-lab quote form.
* ``GET /sc/quote/tissue-row`` — HTMX endpoint that returns a blank
  tissue row partial (used by the "Add row" button on each leg).
* ``GET /sc/quote/tissue-lookup`` — HTMX endpoint that resolves a
  tissue code to its description / unit weight / suggested box type.
* ``GET /sc/quote/lab-lookup`` — HTMX endpoint that resolves a lab code
  to its origin ZIP and address.
* ``GET /sc/reference`` — landing page listing the six SC reference
  tables with download / upload buttons (stubbed in this PR; the
  endpoints land in the next PR).

``POST /sc/quote/calculate`` (the multi-leg orchestration that calls
``app.services.quote.create_quote`` 14 times) is intentionally NOT
included here — it lands in a follow-up PR with its own service module
and the matching results partial.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Union

import pandas as pd
from flask import (
    Response,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.admin import (
    CSVUploadForm,
    _is_missing,
    _parse_bool_flag,
    _parse_csv_rows,
    _parse_optional_float,
    _parse_optional_int,
    _parse_required_float,
    _parse_required_int,
    _parse_required_string,
    _parse_zipcode,
)
from app.models import (
    BOOKING_EMAIL_KIND_SC_MULTI,
    BOOKING_EMAIL_STATUS_PENDING,
    BOOKING_EMAIL_STATUS_SENT,
    RATE_SET_SCIENCE_CARE,
    Accessorial,
    BookingEmailReceipt,
    Quote,
    SCAccessorialMap,
    SCBoxType,
    SCConsumable,
    SCEstablishedLane,
    SCLab,
    SCQuoteSession,
    SCQuoteSessionLeg,
    SCTissueBoxCapacity,
    SCTissueCode,
    SCUserLabSlot,
    db,
)
from app.policies import sc_admin_required, sc_user_required
from app.services.mail import (
    MailRateLimitError,
    booking_email_ops_to,
    record_booking_email_failure,
    send_email,
)
from app.services.science_care_quote import (
    TissueRow,
    _collect_tissue_rows,
    _default_consumable_for_mode,
    _normalize_multi_reference,
    _tissue_box_capacity_index,
    allocate_boxes,
    compute_leg_subtotals,
    compute_sc_multileg,
    recommended_box_for_qty,
)
from app.services.bulk_import import record_rate_upload, save_unique
from app.services.quote import get_zip_notes
from app.services.zip_city_lookup import lookup_city_state

from . import science_care_bp
from .csv_admin import (
    SC_TABLE_SPECS,
    _parse_optional_date,
    _parse_optional_string,
    append_sc_tissue_codes,
    download_sc_tissue_codes_csv,
    force_science_care_rate_set,
    get_sc_table_spec,
    parse_sc_tissue_codes_csv,
    replace_sc_tissue_codes,
)


# Form field → friendly accessorial labels used when the SC tenant has
# not (yet) populated SCAccessorialMap rows. Display-only — the values
# that actually drive pricing live on SCAccessorialMap.accessorial_name
# and must match Accessorial.name (see rates/science_care/sc_accessorial_map.csv).
_FALLBACK_ACCESSORIAL_LABELS = {
    "J3": "4 Hour Delivery/Pick-Up Window",
    "J4": "Special Pickup or Delivery Time",
    "J5": "Afterhours Delivery/Pickup",
    "J6": "Weekend Pickup/Delivery",
    "J7": "Two-Man Team Required",
    "J8": "Liftgate Required",
}

# Number of shipment legs supported by the multi-lab form.
SC_LEG_COUNT = 7


# Maps the sc_tissue_codes TableSpec's virtual per-box column attrs to
# the SCBoxType.code they refer to. Used by ``sc_reference_list`` to
# project SCTissueBoxCapacity rows back into the dedicated Medium /
# Large / X-Large / Small Airtray / Airtray columns the list view
# advertises. Order mirrors :data:`csv_admin.TISSUE_BOX_CAPACITY_HEADERS`.
_TISSUE_BOX_COLUMN_TO_CODE: dict[str, str] = {
    "_pieces_med": "MED",
    "_pieces_lrg": "LRG",
    "_pieces_xlg": "XLG",
    "_pieces_small_airtray": "SMALL_AIRTRAY",
    "_pieces_airtray": "AIRTRAY",
}


def _format_accessorial_cost(
    amount: float | None, is_percentage: bool
) -> str:
    """Return a human-readable cost label like ``"$25.00"`` or ``"5%"``.

    Returns an empty string when ``amount`` is missing or zero so the
    template can suppress the parenthetical entirely for accessorials
    that carry no listed price.
    """

    try:
        value = float(amount or 0.0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if is_percentage:
        if value == int(value):
            return f"{int(value)}%"
        return f"{value:g}%"
    return f"${value:,.2f}"


def _accessorial_labels() -> list[tuple[str, str, str]]:
    """Return ``(form_field, display_label, cost_label)`` triples for the form.

    Reads :class:`app.models.SCAccessorialMap` for the science-care
    rate-set first; falls back to the hard-coded labels so a freshly
    migrated database still renders a usable form. ``cost_label`` is a
    formatted string (e.g. ``"$25.00"`` or ``"5%"``) sourced from the
    matching :class:`app.models.Accessorial` row, or an empty string
    when no priced accessorial maps to the form field.
    """

    cost_by_name: dict[str, tuple[float, bool]] = {
        str(a.name): (float(a.amount or 0.0), bool(a.is_percentage))
        for a in Accessorial.query.all()
    }

    rows = (
        SCAccessorialMap.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(SCAccessorialMap.form_field)
        .all()
    )
    if rows:
        labeled: list[tuple[str, str, str]] = []
        for row in rows:
            amount, is_percentage = cost_by_name.get(
                row.accessorial_name, (0.0, False)
            )
            labeled.append(
                (
                    row.form_field,
                    row.display_label,
                    _format_accessorial_cost(amount, is_percentage),
                )
            )
        return labeled
    return [
        (
            field,
            label,
            _format_accessorial_cost(
                *cost_by_name.get(label, (0.0, False))
            ),
        )
        for field, label in _FALLBACK_ACCESSORIAL_LABELS.items()
    ]


def _lab_choices() -> list[SCLab]:
    """Active SC labs ordered by ``lab_code`` for the datalist."""

    return (
        SCLab.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE, is_active=True
        )
        .order_by(SCLab.lab_code)
        .all()
    )


def _box_type_choices() -> list[SCBoxType]:
    """All SC box types ordered by ``code`` for select rendering."""

    return (
        SCBoxType.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(SCBoxType.code)
        .all()
    )


def _consumable_choices() -> list[SCConsumable]:
    """All SC consumables ordered for per-leg Qty inputs."""

    return (
        SCConsumable.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(
            SCConsumable.temp_mode,
            SCConsumable.scope,
            SCConsumable.consumable_type,
        )
        .all()
    )


def _tissue_code_choices() -> list[SCTissueCode]:
    """All SC tissue codes ordered by ``tissue_code`` for the page datalist."""

    return (
        SCTissueCode.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(SCTissueCode.tissue_code)
        .all()
    )


def _resolve_maps_api_key() -> str:
    """Return the configured Google Maps key for client-side lookups.

    Mirrors the resolution order used by the legacy quote form
    (``app.quotes.routes``) so the SC page picks up the same key without
    a second secret rollout.
    """

    return (
        current_app.config.get("GOOGLE_MAPS_API_KEY")
        or os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("MAPS_API_KEY")
        or ""
    )


def _default_lab_slots(user_id: int) -> dict[int, str]:
    """Return ``{leg_index: lab_code}`` of the user's saved default labs.

    Filters out slots whose ``lab_code`` no longer matches an active
    :class:`SCLab`, so a deactivated lab silently disappears from the
    prefill (the DB row stays - it'll reappear if the lab is
    re-activated).
    """

    rows = (
        db.session.query(SCUserLabSlot, SCLab)
        .join(
            SCLab,
            (SCLab.lab_code == SCUserLabSlot.lab_code)
            & (SCLab.rate_set == SCUserLabSlot.rate_set)
            & (SCLab.is_active.is_(True)),
        )
        .filter(
            SCUserLabSlot.user_id == user_id,
            SCUserLabSlot.rate_set == RATE_SET_SCIENCE_CARE,
        )
        .all()
    )
    return {slot.leg_index: slot.lab_code for slot, _lab in rows}


@science_care_bp.get("/quote")
@login_required
@sc_user_required
def sc_quote_form() -> str:
    """Render the empty seven-leg multi-lab quote form.

    When called with ``?from_session=<id>`` or ``?from_ref=<multi_ref>``
    the form is prefilled from a previously-submitted multi-leg quote so
    the SC user can edit fields and submit a brand-new run. The original
    session is never modified; only ``payload_json`` is re-projected
    into the inputs.
    """

    prefill_payload = _resolve_sc_edit_prefill(request.args)
    g.sc_prefill = prefill_payload or {}

    box_types = _box_type_choices()
    consumables = _consumable_choices()
    prefill_results = None
    prefill_tissue_rows_by_leg: dict[int, list[dict]] = {}
    if prefill_payload:
        tissue_index, _, capacity_index = _sc_reference_indexes_for_prefill()
        box_index_by_code = {b.code: b for b in box_types}
        box_index_by_id = {int(b.id): b for b in box_types}
        prefill_results = _build_prefill_results(
            prefill_payload, box_index_by_id, consumables
        )
        for n in range(1, SC_LEG_COUNT + 1):
            rows = _build_prefill_tissue_rows(
                prefill_payload,
                n,
                tissue_index,
                box_index_by_code,
                capacity_index,
            )
            if rows:
                prefill_tissue_rows_by_leg[n] = rows

    return render_template(
        "sc/quote.html",
        leg_count=SC_LEG_COUNT,
        legs=list(range(1, SC_LEG_COUNT + 1)),
        accessorials=_accessorial_labels(),
        labs=_lab_choices(),
        box_types=box_types,
        tissue_codes=_tissue_code_choices(),
        consumables=consumables,
        default_labs_by_leg=_default_lab_slots(current_user.id),
        maps_api_key=_resolve_maps_api_key(),
        prefill_results=prefill_results,
        prefill_tissue_rows_by_leg=prefill_tissue_rows_by_leg,
    )


# Minimal LegResult-shaped object the existing jinja helpers
# (sc_box_values_for_leg / sc_consumable_values_for_leg /
# sc_initial_subtotals_for_leg) consume. Only the prefill-relevant
# columns are populated — weights stay at 0 because the live HTMX
# endpoints recompute them on the next form interaction.
@dataclass
class _SyntheticPrefillLeg:
    box_counts: dict[str, int] = field(default_factory=dict)
    consumable_picks: dict[int, int] = field(default_factory=dict)
    tissue_weight_lb: float = 0.0
    consumable_weight_lb: float = 0.0
    box_tare_weight_lb: float = 0.0
    total_weight_lb: float = 0.0


@dataclass
class _SyntheticPrefillResults:
    legs: list[_SyntheticPrefillLeg]


def _resolve_sc_edit_prefill(args) -> dict[str, str] | None:
    """Resolve ``?from_session`` / ``?from_ref`` into a saved form payload.

    Returns ``None`` when neither arg is present, the lookup fails, or
    the persisted JSON is unparseable. ``multi_reference`` is stripped
    so the new submission either auto-assigns the next SCMQ or the user
    types a fresh value — leaving the old one in place would just trip
    the UNIQUE constraint on submit.
    """

    raw_id = (args.get("from_session") or "").strip()
    raw_ref = (args.get("from_ref") or "").strip()
    if not raw_id and not raw_ref:
        return None

    session = None
    if raw_id:
        try:
            session_id = int(raw_id)
        except ValueError:
            flash("Invalid session ID for prefill.", "warning")
            return None
        session = SCQuoteSession.query.filter_by(id=session_id).first()
    if session is None and raw_ref:
        ref, _err = _normalize_multi_reference(raw_ref)
        if ref:
            session = SCQuoteSession.query.filter_by(
                multi_reference=ref
            ).first()
    if session is None:
        flash("Could not find the multi-leg quote to prefill.", "warning")
        return None

    payload = _safe_json_load(session.payload_json)
    if not isinstance(payload, dict):
        flash(
            "Multi-leg quote has no replayable form payload.", "warning"
        )
        return None

    flash(
        f"Loaded multi-leg {session.multi_reference}. Edit any field "
        "and submit to run a new quote.",
        "info",
    )
    return {
        str(k): str(v)
        for k, v in payload.items()
        if k != "multi_reference"
    }


def _sc_reference_indexes_for_prefill():
    """Pre-cache the tissue / box-code / capacity indexes used by prefill."""

    tissue_index = {
        t.tissue_code: t
        for t in SCTissueCode.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    box_index_by_code = {
        b.code: b
        for b in SCBoxType.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    capacity_index = _tissue_box_capacity_index()
    return tissue_index, box_index_by_code, capacity_index


def _build_prefill_results(
    payload: Mapping[str, str],
    box_index_by_id: dict[int, SCBoxType],
    consumable_index: list[SCConsumable],
) -> _SyntheticPrefillResults:
    """Project the saved form payload into synthetic per-leg LegResults.

    Reads ``box_count_<leg>_<box_id>`` and ``cons_qty_<leg>_<cons_id>``
    keys for every leg and stores them keyed the way
    :func:`sc_box_values_for_leg` /
    :func:`sc_consumable_values_for_leg` expect (box code → count and
    consumable id → qty). Empty / zero values are skipped so the
    template's existing "only render non-zero" rules apply unchanged.
    """

    legs: list[_SyntheticPrefillLeg] = []
    for n in range(1, SC_LEG_COUNT + 1):
        box_counts: dict[str, int] = {}
        for box_id, box in box_index_by_id.items():
            raw = payload.get(f"box_count_{n}_{box_id}")
            try:
                count = int(str(raw).strip()) if raw not in (None, "") else 0
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                box_counts[box.code] = count

        cons_picks: dict[int, int] = {}
        for cons in consumable_index:
            raw = payload.get(f"cons_qty_{n}_{cons.id}")
            try:
                qty = int(str(raw).strip()) if raw not in (None, "") else 0
            except (TypeError, ValueError):
                qty = 0
            if qty > 0:
                cons_picks[int(cons.id)] = qty

        legs.append(
            _SyntheticPrefillLeg(
                box_counts=box_counts, consumable_picks=cons_picks
            )
        )
    return _SyntheticPrefillResults(legs=legs)


def _build_prefill_tissue_rows(
    payload: Mapping[str, str],
    leg: int,
    tissue_index: dict[str, SCTissueCode],
    box_index_by_code: dict[str, SCBoxType],
    capacity_index: dict[str, dict[str, int]],
) -> list[dict]:
    """Translate persisted tissue rows into the partial's context dicts.

    Walks ``tissue_code_<leg>_<i>`` / ``qty_<leg>_<i>`` keys via the same
    :func:`_collect_tissue_rows` helper the orchestrator uses, then
    builds one context dict per row in the shape ``_tissue_row.html``
    expects (``prefill`` SCTissueCode object, ``code`` typed string,
    ``qty``, ``capacities``, ``recommended_box_code``,
    ``recommended_pieces_per_box``, ``user_box_pick``). Empty list when
    the leg had no tissue rows in the saved payload — the template
    falls back to rendering one blank row.
    """

    rows = _collect_tissue_rows(payload, leg)
    out: list[dict] = []
    for tr in rows:
        capacities = capacity_index.get(tr.tissue_code, {})
        recommended_box, recommended_per_box = recommended_box_for_qty(
            tr.qty if tr.qty > 0 else 1,
            capacities,
            box_index_by_code,
        )
        out.append(
            {
                "tissue_obj": tissue_index.get(tr.tissue_code),
                "tissue_code": tr.tissue_code,
                "qty": tr.qty,
                "capacities": capacities,
                "recommended_box_code": recommended_box,
                "recommended_pieces_per_box": recommended_per_box,
                "user_box_pick": tr.user_box_code,
            }
        )
    return out


@science_care_bp.post("/quote/calculate")
@login_required
@sc_user_required
def sc_quote_calculate() -> str:
    """HTMX endpoint: run the multi-leg orchestration and swap a partial.

    Returns the ``sc/_results_partial.html`` fragment for HTMX swap.
    Non-HTMX callers (the standard form ``POST /sc/quote`` endpoint)
    can use the same result context.
    """

    try:
        context = compute_sc_multileg(
            request.form, current_user, request.remote_addr
        )
    except ValueError as exc:
        # Reference validation / duplicate. Render an inline error in
        # the results card so HTMX swaps a usable message in place.
        return render_template(
            "sc/_results_error.html", error_message=str(exc)
        )
    return render_template("sc/_results_partial.html", **context)


@science_care_bp.post("/quote")
@login_required
@sc_user_required
def sc_quote_submit():
    """Non-HTMX fallback for ``POST /sc/quote``.

    Renders the full quote page with the results card filled in. Used
    when the browser submits the form via the standard ``Enter`` /
    button path instead of HTMX.
    """

    try:
        context = compute_sc_multileg(
            request.form, current_user, request.remote_addr
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        context = None
    return render_template(
        "sc/quote.html",
        leg_count=SC_LEG_COUNT,
        legs=list(range(1, SC_LEG_COUNT + 1)),
        accessorials=_accessorial_labels(),
        labs=_lab_choices(),
        box_types=_box_type_choices(),
        tissue_codes=_tissue_code_choices(),
        consumables=_consumable_choices(),
        default_labs_by_leg=_default_lab_slots(current_user.id),
        maps_api_key=_resolve_maps_api_key(),
        results=context,
    )


@science_care_bp.get("/quote/tissue-row")
@login_required
@sc_user_required
def sc_tissue_row_partial() -> str:
    """Return a blank tissue-row partial for HTMX append.

    Query parameters:
        leg: 1-based leg index (clamped to ``[1, SC_LEG_COUNT]``).
        i:   row index within the leg's tissue table (clamped to >= 1).
    """

    leg = max(1, min(SC_LEG_COUNT, _safe_int(request.args.get("leg"), 1)))
    row_index = max(1, _safe_int(request.args.get("i"), 1))

    return render_template(
        "sc/_tissue_row.html",
        leg=leg,
        i=row_index,
        prefill=None,
        code="",
        qty_for_row=0,
        box_types=_box_type_choices(),
        capacities={},
        recommended_box_code=None,
        recommended_pieces_per_box=0,
        user_box_pick="",
    )


def _safe_int(value: object, default: int) -> int:
    """Coerce ``value`` to int, falling back to ``default`` on any error.

    Keeps the tissue / lab partial endpoints from raising a 500 when a
    user hand-edits the query string (``?leg=abc``) or when HTMX races
    a request with a partially typed value.
    """

    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@science_care_bp.get("/quote/tissue-lookup")
@login_required
@sc_user_required
def sc_tissue_lookup_partial() -> str:
    """Return a tissue row partial pre-filled from a tissue code.

    Query parameters:
        leg: 1-based leg index.
        i:   row index within the leg's tissue table.
        code: SC tissue code (case-insensitive lookup). HTMX sends the
            input's ``name`` (e.g. ``tissue_code_3_2``) rather than
            ``code``, so the dynamic name is checked as a fallback.
    """

    leg = max(1, min(SC_LEG_COUNT, _safe_int(request.args.get("leg"), 1)))
    row_index = max(1, _safe_int(request.args.get("i"), 1))

    code = request.args.get("code")
    if not code:
        code = request.args.get(f"tissue_code_{leg}_{row_index}")
    code = (code or "").strip().upper()

    prefill: SCTissueCode | None = None
    if code:
        prefill = (
            SCTissueCode.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE, tissue_code=code
            ).first()
        )

    box_types = _box_type_choices()
    box_index = {b.code: b for b in box_types}

    # Load this tissue's per-box capacities so the row dropdown can
    # advertise the box sizes that actually fit it (and so the auto-pick
    # below uses the right pieces_per_box).
    capacity_index: dict[str, dict[str, int]] = {}
    if code:
        capacity_index = _tissue_box_capacity_index([code])
    capacities_for_row = dict(capacity_index.get(code, {}))

    # Legacy tenants without capacity rows still have a single
    # default_box_type_code on SCTissueCode. Surface it in the dropdown
    # so the page renders the same way until they upload the expanded
    # CSV.
    if (
        prefill is not None
        and not capacities_for_row
        and prefill.default_box_type_code
        and prefill.default_box_type_code in box_index
    ):
        capacities_for_row[prefill.default_box_type_code] = max(
            1, int(prefill.pieces_per_box or 1)
        )

    qty_for_row = max(0, _safe_int(
        request.args.get(f"qty_{leg}_{row_index}"), 1 if prefill else 0
    ))
    recommended_box, recommended_per_box = recommended_box_for_qty(
        max(qty_for_row, 1) if prefill else qty_for_row,
        capacities_for_row,
        box_index,
    )

    # Carry the user's existing box pick (if any) through the partial so
    # an HTMX-driven re-render (qty change, etc.) does not reset their
    # selection. Empty means "auto" - the template selects the
    # recommended box visually.
    user_box_pick = (
        request.args.get(f"box_choice_{leg}_{row_index}") or ""
    ).strip().upper()

    tissue_row_html = render_template(
        "sc/_tissue_row.html",
        leg=leg,
        i=row_index,
        prefill=prefill,
        code=code,
        qty_for_row=qty_for_row,
        box_types=box_types,
        capacities=capacities_for_row,
        recommended_box_code=recommended_box,
        recommended_pieces_per_box=recommended_per_box,
        user_box_pick=user_box_pick,
    )

    # OOB recompute of the leg's box counts AND its weight subtotals
    # card. Mirrors the qty trigger in /sc/quote/leg/<n>/box-counts but
    # piggy-backs on this response so swapping a tissue code (e.g.
    # ARM01 -> PELV03) updates the Boxes section + subtotals in the
    # same round-trip. `request.args` carries the full leg body via
    # the input's hx-include.
    #
    # The freshly-typed code may not yet be reflected in request.args
    # because the user is still editing the input. Splice it in so the
    # OOB box allocation reflects what the visible tissue row will show
    # after the swap completes. Same for the defaulted qty: the rendered
    # row gets qty=1 the moment prefill matches, so the OOB subtotals
    # need to see qty=1 too or the Shipment-weight card + section pills
    # stay at 0 lb until the user touches the qty input.
    args = request.args.copy()
    if prefill:
        args[f"tissue_code_{leg}_{row_index}"] = prefill.tissue_code
        if qty_for_row > 0:
            args[f"qty_{leg}_{row_index}"] = str(qty_for_row)
    box_counts_html, subtotals_html = _render_box_counts_and_subtotals(
        args, leg, box_types, box_index, oob=True
    )
    return tissue_row_html + box_counts_html + subtotals_html


@science_care_bp.get("/quote/dest-zip-notes")
@login_required
@sc_user_required
def sc_dest_zip_notes_partial() -> str:
    """Return the destination ZIP shipment-notes banner for one leg.

    Triggered by the destination ZIP input on each leg so the operator
    sees the source workbook's ZIP-specific cargo warnings (airport
    restrictions, airtray weekend rules, etc.) the moment they finish
    typing the ZIP. Notes come from :class:`app.models.ZipZone` via
    :func:`app.services.quote.get_zip_notes`.

    Query parameters:
        leg: 1-based leg index (used to scope the swap target).
        zip: Destination ZIP. HTMX sends the input's ``name``
            (``dest_zip_<leg>``) rather than ``zip``, so the dynamic
            name is checked as a fallback.
    """

    leg = max(1, min(SC_LEG_COUNT, _safe_int(request.args.get("leg"), 1)))

    zip_code = request.args.get("zip")
    if not zip_code:
        zip_code = request.args.get(f"dest_zip_{leg}")
    zip_code = (zip_code or "").strip()

    # The dest-ZIP input is `pattern="\d{5}" maxlength="5"`, so any value
    # shorter than 5 chars is mid-typing and can't possibly match a
    # ZipZone row. Skip the DB round-trip until the operator has a full
    # ZIP - HTMX fires this endpoint on every keystroke.
    notes: str | None = None
    if len(zip_code) == 5:
        notes = get_zip_notes(
            zip_code, RATE_SET_SCIENCE_CARE, session=db.session
        )

    return render_template(
        "sc/_dest_zip_notes.html",
        leg=leg,
        zip_code=zip_code,
        notes=notes,
    )


@science_care_bp.get("/quote/lab-lookup")
@login_required
@sc_user_required
def sc_lab_lookup_partial() -> str:
    """Return an origin-zip readout partial for one leg.

    Query parameters:
        leg:  1-based leg index (used to scope the swap target).
        code: SC lab code (case-insensitive lookup). HTMX sends the
            input's ``name`` (``lab_code_<leg>``) rather than ``code``,
            so the dynamic name is checked as a fallback.
    """

    leg = max(1, min(SC_LEG_COUNT, _safe_int(request.args.get("leg"), 1)))

    code = request.args.get("code")
    if not code:
        code = request.args.get(f"lab_code_{leg}")
    code = (code or "").strip().upper()

    lab: SCLab | None = None
    if code:
        lab = (
            SCLab.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE,
                lab_code=code,
                is_active=True,
            ).first()
        )
    return render_template(
        "sc/_lab_lookup.html", leg=leg, lab=lab, code=code
    )


def _tissue_index_for_rows(
    tissue_rows: list[TissueRow],
) -> dict[str, SCTissueCode]:
    """Fetch only the SCTissueCode rows present in ``tissue_rows``.

    The live-recompute endpoints fire on every keystroke, so loading
    the full ``SCTissueCode`` table each time scales badly as the
    reference data grows. Scope the query to the codes actually used
    in the current leg's tissue rows.

    The codes are sorted before being passed to ``.in_()`` so the SQL
    parameter binding is deterministic across requests — matches the
    pattern used by ``SCUserLabSlot`` queries elsewhere in this module
    and helps the database reuse prepared-statement plans.
    """

    codes = {r.tissue_code for r in tissue_rows if r.tissue_code}
    if not codes:
        return {}
    return {
        t.tissue_code: t
        for t in SCTissueCode.query.filter(
            SCTissueCode.rate_set == RATE_SET_SCIENCE_CARE,
            SCTissueCode.tissue_code.in_(sorted(codes)),
        ).all()
    }


def _resolve_box_values(
    form, leg: int, box_types, auto_boxes_by_type
) -> dict:
    """Combine typed box-count overrides with the auto allocation.

    "Prefill empty inputs only": any non-blank typed value wins
    (including a deliberate "0", which means "no boxes of this
    type"). Only a truly blank input falls back to the auto count.

    Shared by both /sc/quote/leg/<n>/box-counts (qty trigger) and
    /sc/quote/tissue-lookup (tissue-code trigger via OOB swap) so
    they can't drift.
    """

    box_values: dict[int, int] = {}
    for box in box_types:
        raw_val = form.get(f"box_count_{leg}_{box.id}")
        if raw_val is not None and str(raw_val).strip() != "":
            box_values[int(box.id)] = _safe_int(raw_val, 0)
            continue
        auto_count = int(auto_boxes_by_type.get(box.code, 0))
        if auto_count > 0:
            box_values[int(box.id)] = auto_count
    return box_values


def _resolve_consumable_values(
    form: Mapping[str, str],
    leg: int,
    consumable_index: list[SCConsumable],
    *,
    total_boxes: int,
    temp_mode: str,
) -> dict[int, str]:
    """Return the per-input display map for the consumables Qty grid.

    Mirrors :func:`_resolve_box_values` for the consumables row so the
    HTMX partial can re-render the grid alongside the box-count + weight
    OOB swaps. Semantics match
    :func:`app.services.science_care_quote._consumable_picks_from_form`:

    * A non-blank typed value wins (including an explicit ``"0"``) and
      is preserved verbatim so the user's suppression / override
      survives the swap.
    * A blank input for the consumable that
      :func:`_default_consumable_for_mode` returns for ``temp_mode``
      falls back to ``str(total_boxes)`` so the field visibly shows the
      auto-applied count rather than reading ``0`` while the subtotal
      includes its weight.
    * Non-matching consumables stay out of the dict so the template
      renders them blank - the placeholder ``0`` keeps the page tidy.
    """

    default = _default_consumable_for_mode(temp_mode, consumable_index)
    default_id = default.id if default is not None else None
    out: dict[int, str] = {}
    for cons in consumable_index:
        raw = form.get(f"cons_qty_{leg}_{cons.id}")
        if raw is not None and str(raw).strip() != "":
            out[int(cons.id)] = str(raw).strip()
            continue
        if cons.id == default_id and total_boxes > 0:
            out[int(cons.id)] = str(total_boxes)
    return out


@science_care_bp.post("/quote/leg/<int:leg>/box-counts")
@login_required
@sc_user_required
def sc_box_counts_partial(leg: int) -> str:
    """HTMX endpoint: live-recompute one leg's box-count inputs.

    Triggered whenever a tissue row's qty input changes. The endpoint
    receives the full leg body via ``hx-include="#sc-leg-<n>"`` and:

    * Parses tissue rows with :func:`_collect_tissue_rows`.
    * Calls :func:`allocate_boxes` (no overrides) to get the auto
      ``{box_code: count}`` allocation that's implied by those rows.
    * Hands off to :func:`_resolve_box_values` to combine the auto
      counts with the user's typed overrides ("prefill empty inputs
      only", explicit "0" preserved).
    * Returns the partial wrapping ``<div id="box-counts-<leg>">`` so
      HTMX swaps the whole grid in place via ``hx-swap="outerHTML"``.
    """

    leg = max(1, min(SC_LEG_COUNT, _safe_int(leg, 1)))

    box_types = _box_type_choices()
    box_index = {b.code: b for b in box_types}

    box_counts_html, subtotals_html = _render_box_counts_and_subtotals(
        request.form, leg, box_types, box_index, oob=False
    )
    # box-counts swaps target #box-counts-<leg> via outerHTML; subtotals
    # ride along as an OOB swap so HTMX repaints the weight card too.
    return box_counts_html + subtotals_html


def _render_box_counts_and_subtotals(
    form: Mapping[str, str],
    leg: int,
    box_types,
    box_index: dict[str, SCBoxType],
    oob: bool,
) -> tuple[str, str]:
    """Render the box-counts grid + the weight subtotals card for ``leg``.

    Shared by ``sc_box_counts_partial`` (qty / box override / consumable
    Qty / temp_mode triggers) and ``sc_tissue_lookup_partial`` (tissue
    code change). Returning both halves as a tuple keeps the two
    endpoints from drifting on the allocation + subtotal computation.

    When ``oob`` is True, the box-counts partial is rendered with
    ``oob=True`` so HTMX swaps it into ``#box-counts-<leg>``. The
    subtotals partial is always emitted with ``oob=True`` because it
    swaps into a different target (``#sc-weight-subtotals-<leg>``).
    """

    tissue_rows = _collect_tissue_rows(form, leg)
    tissue_index = _tissue_index_for_rows(tissue_rows)
    capacity_index = _tissue_box_capacity_index(
        [r.tissue_code for r in tissue_rows]
    )
    # allocate_boxes (without overrides) populates per-row unit_weight_lb
    # AND returns the auto allocation. Per-input typed overrides win in
    # _resolve_box_values below ("prefill empty inputs only" semantic);
    # we then re-key the displayed values back to box-code form to feed
    # the subtotal's box_tare math.
    _, _, auto_boxes_by_type, _, _ = allocate_boxes(
        tissue_rows,
        tissue_index,
        box_index,
        capacity_index=capacity_index,
    )

    box_values = _resolve_box_values(
        form, leg, box_types, auto_boxes_by_type
    )
    box_counts_html = render_template(
        "sc/_box_count_inputs.html",
        leg=leg,
        box_types=box_types,
        box_values=box_values,
        oob=oob,
    )

    # Rebuild boxes_by_type from the merged values so the subtotal's
    # box-tare row matches what the user is looking at in the Boxes
    # section. _resolve_box_values keys by box.id; flip back to code.
    final_boxes_by_type: dict[str, int] = {}
    for box in box_types:
        count = int(box_values.get(int(box.id), 0))
        if count > 0:
            final_boxes_by_type[box.code] = count

    consumable_index = _consumable_choices()
    subtotals = compute_leg_subtotals(
        form,
        leg,
        tissue_rows,
        final_boxes_by_type,
        box_index,
        consumable_index,
    )
    subtotals_html = render_template(
        "sc/_leg_weight_subtotals.html",
        leg=leg,
        subtotals=subtotals,
        oob=True,
    )
    # OOB-swap the consumable Qty grid so the matching temp_mode default
    # row visibly reflects the new box count - the subtotal already
    # includes its weight via compute_leg_subtotals, so leaving the
    # input blank made the page lie about what was being charged.
    # Suppress the swap when the trigger is the user typing in a
    # consumable input itself, otherwise the OOB replace would steal
    # focus mid-keystroke and make multi-digit overrides unusable.
    total_boxes = int(sum(final_boxes_by_type.values()))
    temp_mode = (form.get(f"temp_mode_{leg}") or "").strip()
    consumable_values = _resolve_consumable_values(
        form,
        leg,
        consumable_index,
        total_boxes=total_boxes,
        temp_mode=temp_mode,
    )
    trigger_id = request.headers.get("HX-Trigger") or ""
    if trigger_id.startswith(f"cons_qty_{leg}_"):
        cons_inputs_html = ""
    else:
        cons_inputs_html = render_template(
            "sc/_consumable_qty_inputs.html",
            leg=leg,
            consumables=consumable_index,
            consumable_values=consumable_values,
            oob=True,
        )
    # Per-section subtotal pills (Consumables / Tissue items / Boxes)
    # each live under their respective fieldset and are swapped
    # individually via OOB so the user sees the same numbers without
    # having to scroll to the bottom recap card.
    section_html = "".join(
        render_template(
            "sc/_section_subtotal.html",
            leg=leg,
            section=section,
            label=label,
            weight_lb=subtotals[weight_key],
            oob=True,
        )
        for section, label, weight_key in (
            ("consumable", "Consumables", "consumable_lb"),
            ("tissue", "Tissue", "tissue_lb"),
            ("box", "Box tare", "box_tare_lb"),
        )
    )
    return box_counts_html, subtotals_html + section_html + cons_inputs_html


@science_care_bp.get("/quote/defaults")
@login_required
@sc_user_required
def sc_lab_defaults_form() -> str:
    """Render the per-user default-labs management page."""

    return render_template(
        "sc/lab_defaults.html",
        leg_count=SC_LEG_COUNT,
        legs=list(range(1, SC_LEG_COUNT + 1)),
        labs=_lab_choices(),
        default_labs_by_leg=_default_lab_slots(current_user.id),
    )


@science_care_bp.post("/quote/defaults")
@login_required
@sc_user_required
def sc_lab_defaults_save():
    """Replace the user's default-lab slots from the submitted form.

    Wipes every existing :class:`SCUserLabSlot` row for the current user
    in the SC rate-set and inserts one row per non-blank ``lab_code_<n>``
    field. Wrapped in try/rollback so a constraint failure doesn't leak
    a half-applied transaction back to the next request.
    """

    valid_lab_codes = {lab.lab_code for lab in _lab_choices()}
    new_rows: list[SCUserLabSlot] = []
    for n in range(1, SC_LEG_COUNT + 1):
        code = (request.form.get(f"lab_code_{n}") or "").strip().upper()
        if not code:
            continue
        if code not in valid_lab_codes:
            # Silently drop unknown labs - the management form already
            # constrains the picker via datalist, and bouncing the whole
            # save for one typo would be hostile.
            continue
        new_rows.append(
            SCUserLabSlot(
                user_id=current_user.id,
                leg_index=n,
                lab_code=code,
                rate_set=RATE_SET_SCIENCE_CARE,
            )
        )

    try:
        # Scope the wipe to slots whose lab_code is currently active.
        # Rows pointing at a temporarily-deactivated lab are hidden by
        # _default_lab_slots() and therefore can't be expressed on the
        # form - leaving them out of the delete preserves them so the
        # user's default reappears when the lab is reactivated.
        # `sorted(...)` keeps the SQL parameter ordering deterministic
        # (better query-plan caching); the empty-set guard skips a
        # pointless DELETE WHERE lab_code IN () round-trip on a fresh
        # SC tenant.
        if valid_lab_codes:
            SCUserLabSlot.query.filter(
                SCUserLabSlot.user_id == current_user.id,
                SCUserLabSlot.rate_set == RATE_SET_SCIENCE_CARE,
                SCUserLabSlot.lab_code.in_(sorted(valid_lab_codes)),
            ).delete(synchronize_session=False)
        db.session.flush()
        if new_rows:
            # add_all over bulk_save_objects: we have at most SC_LEG_COUNT
            # rows, so the unit-of-work overhead is irrelevant and we keep
            # the standard SQLAlchemy state tracking.
            db.session.add_all(new_rows)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - re-surfaced on flash
        db.session.rollback()
        flash(f"Could not save defaults: {exc}", "danger")
        return redirect(url_for("science_care.sc_lab_defaults_form"))

    flash(
        f"Saved {len(new_rows)} default lab slot(s).", "success"
    )
    return redirect(url_for("science_care.sc_quote_form"))


# --- Booking email & lookup -------------------------------------------------


def _load_sc_session_or_404(session_id: int) -> SCQuoteSession:
    """Fetch an :class:`SCQuoteSession` by primary key or abort 404.

    Used by the booking-email render path. SC scope is enforced by the
    ``@sc_user_required`` guard on the route - we deliberately do NOT
    filter by ``current_user.id`` because operations on a multi-leg
    quote (lookup, ops-email) may be initiated by a different SC user
    helping a customer find their previously generated shipment.
    """

    session = SCQuoteSession.query.filter_by(id=session_id).first()
    if session is None:
        abort(404)
    return session


def _load_sc_session_legs(session: SCQuoteSession) -> list[SCQuoteSessionLeg]:
    """Return the leg rows for ``session`` ordered by ``leg_index``."""

    return (
        SCQuoteSessionLeg.query.filter_by(session_id=session.id)
        .order_by(SCQuoteSessionLeg.leg_index)
        .all()
    )


def _safe_json_load(raw: str | None) -> Any:
    """``json.loads(raw)`` that returns ``None`` on missing / bad input.

    Used to defuse the ``payload_json`` / ``boxes_json`` /
    ``consumables_json`` columns on historical SCQuoteSession rows:
    NULL is expected (pre-feature legs) and a malformed value would
    otherwise 500 the booking-email page.
    """

    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _lab_city_state(lab: SCLab | None) -> tuple[str, str]:
    """Return ``(city, state)`` for a lab, falling back to zip lookup.

    Prefer the lab's own ``address`` (last "City, ST" segment) when the
    SC admin filled it in, otherwise resolve via ``Zipcode_Zones.csv``
    using the lab's origin ZIP. Both values returned uppercase to match
    the destination side rendered from ``lookup_city_state``.
    """

    if lab is None:
        return "", ""
    if lab.address:
        parts = [p.strip() for p in lab.address.split(",") if p.strip()]
        if len(parts) >= 2:
            city = parts[-2].upper()
            state_tokens = parts[-1].split()
            if state_tokens:
                state = state_tokens[0].upper()
                if len(state) == 2:
                    return city, state
    city_state = lookup_city_state(lab.origin_zip)
    if city_state is not None:
        return city_state
    return "", ""


def _hydrate_legs_for_display(
    legs: list[SCQuoteSessionLeg],
    session: SCQuoteSession | None = None,
) -> list[dict]:
    """Join each persisted leg with its winning :class:`Quote` row.

    Returns a list of dicts shaped for ``sc/email_ops_request.html`` and
    ``sc/_lookup_summary.html``: origin/destination ZIP, billable
    weight, winner mode/total, and a status string. Skipped legs are
    included so the booking email + lookup page can show why a leg has
    no price.

    When ``session`` is supplied, each dict is also enriched with the
    booking-detail fields the ops summary email needs: origin lab name
    + city, destination city/state, the list of accessorial display
    labels, and per-leg summaries of the tissue items, boxes, and
    consumables the user submitted. These come from
    ``session.payload_json`` (the original form) plus the JSON columns
    on each :class:`SCQuoteSessionLeg`. The lookup-page caller passes
    ``session`` too so the same enriched dicts can be reused there.
    """

    quote_ids = {
        leg.air_quote_id
        for leg in legs
        if leg.air_quote_id is not None
    } | {
        leg.hotshot_quote_id
        for leg in legs
        if leg.hotshot_quote_id is not None
    }
    quotes_by_id: dict[int, Quote] = {}
    if quote_ids:
        quotes_by_id = {
            q.id: q
            for q in Quote.query.filter(Quote.id.in_(sorted(quote_ids))).all()
        }

    payload: Mapping[str, str] = {}
    if session is not None:
        raw_payload = _safe_json_load(session.payload_json)
        if isinstance(raw_payload, dict):
            payload = {str(k): str(v) for k, v in raw_payload.items()}

    # Pre-cache the lookup tables once per request so the per-leg loop
    # doesn't re-query the SC reference tables for each leg.
    lab_index: dict[str, SCLab] = {}
    accessorial_map: dict[str, SCAccessorialMap] = {}
    tissue_index: dict[str, SCTissueCode] = {}
    box_index_by_code: dict[str, SCBoxType] = {}
    consumable_index_by_id: dict[int, SCConsumable] = {}
    if session is not None:
        # Code/key columns are uppercased on every lookup path (form
        # parsing, CSV import, orchestrator) so normalize the pre-cache
        # too - a tenant who saved a mixed-case row directly via the
        # admin form would otherwise silently fail to match.
        lab_index = {
            lab.lab_code.upper(): lab
            for lab in SCLab.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE
            ).all()
            if lab.lab_code
        }
        accessorial_map = {
            a.form_field: a
            for a in SCAccessorialMap.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE
            ).all()
        }
        tissue_index = {
            t.tissue_code.upper(): t
            for t in SCTissueCode.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE
            ).all()
            if t.tissue_code
        }
        box_index_by_code = {
            b.code.upper(): b
            for b in SCBoxType.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE
            ).all()
            if b.code
        }
        consumable_index_by_id = {
            int(c.id): c
            for c in SCConsumable.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE
            ).all()
        }

    out: list[dict] = []
    for leg in legs:
        air = quotes_by_id.get(leg.air_quote_id) if leg.air_quote_id else None
        hot = (
            quotes_by_id.get(leg.hotshot_quote_id)
            if leg.hotshot_quote_id
            else None
        )
        # Prefer the winning mode's Quote for origin/destination/weight
        # display so the booking email mirrors the row the user picked.
        # Fall back to whichever Quote is non-null otherwise.
        primary = (
            hot if (leg.winner_mode or "").lower() == "hotshot" else air
        ) or air or hot

        row: dict[str, Any] = {
            "leg_index": leg.leg_index,
            "origin": primary.origin if primary else "",
            "destination": primary.destination if primary else "",
            "weight": float(primary.weight) if primary else 0.0,
            "winner_mode": leg.winner_mode,
            "winner_total": float(leg.winner_total or 0.0),
            "skip_reason": leg.skip_reason,
            "air_total": float(air.total) if air and air.total else None,
            "hotshot_total": (
                float(hot.total) if hot and hot.total else None
            ),
            "established_rate": (
                float(leg.established_rate)
                if leg.established_rate is not None
                else None
            ),
            "lab_code": "",
            "lab_name": "",
            "lab_address": "",
            "lab_city": "",
            "lab_state": "",
            "dest_city": "",
            "dest_state": "",
            "accessorials": [],
            "tissue_items": [],
            "boxes": [],
            "consumables": [],
            # Per-segment weight subtotals so the booking email body can
            # group tissue / boxes / consumables under their own
            # subtotal lines and finish with an overall shipment weight
            # summary. Always sums to ``shipment_weight_lb`` and matches
            # the LegResult breakdown documented in docs/equations.md
            # (EQ-013).
            "tissue_weight_lb": 0.0,
            "boxes_weight_lb": 0.0,
            "consumables_weight_lb": 0.0,
            "shipment_weight_lb": 0.0,
        }

        if session is None:
            out.append(row)
            continue

        lab_code = (
            payload.get(f"lab_code_{leg.leg_index}") or ""
        ).strip().upper()
        row["lab_code"] = lab_code
        lab = lab_index.get(lab_code) if lab_code else None
        if lab is not None:
            row["lab_name"] = lab.lab_name or ""
            row["lab_address"] = lab.address or ""
            row["lab_city"], row["lab_state"] = _lab_city_state(lab)

        dest_zip = row["destination"] or (
            payload.get(f"dest_zip_{leg.leg_index}") or ""
        ).strip()
        dest_city_state = lookup_city_state(dest_zip)
        if dest_city_state is not None:
            row["dest_city"], row["dest_state"] = dest_city_state

        accessorials: list[str] = []
        suffix = f"_{leg.leg_index}"
        for key, value in payload.items():
            if not key.startswith("acc_") or not key.endswith(suffix):
                continue
            if str(value).strip().upper() not in {"Y", "ON", "TRUE", "1"}:
                continue
            form_field = key[len("acc_") : -len(suffix)]
            if not form_field:
                continue
            mapping = accessorial_map.get(form_field)
            if mapping is not None:
                # Display label is friendlier than accessorial_name for
                # an ops-facing email - it's what the user saw on the
                # form. Fall back to the field code so a tenant whose
                # SCAccessorialMap is incomplete still sees something.
                accessorials.append(
                    mapping.display_label or mapping.accessorial_name
                )
            else:
                fallback = _FALLBACK_ACCESSORIAL_LABELS.get(form_field)
                accessorials.append(fallback or form_field)
        row["accessorials"] = accessorials

        # Tissue items: walk the same tissue_code_<leg>_<i> /
        # qty_<leg>_<i> pairs the orchestrator parsed at submit time so
        # the email's tissue list mirrors what the user picked.
        tissue_rows = _collect_tissue_rows(payload, leg.leg_index)
        for tr in tissue_rows:
            if not tr.tissue_code:
                continue
            tissue = tissue_index.get(tr.tissue_code)
            unit_weight_lb = (
                float(tissue.unit_weight_lb)
                if tissue is not None
                and tissue.unit_weight_lb is not None
                else None
            )
            line_weight_lb = (
                (unit_weight_lb or 0.0) * float(tr.qty or 0)
            )
            row["tissue_items"].append(
                {
                    "code": tr.tissue_code,
                    "description": (
                        tissue.description if tissue is not None else ""
                    ) or "",
                    "qty": tr.qty,
                    "unit_weight_lb": unit_weight_lb,
                    "line_weight_lb": line_weight_lb,
                }
            )
            row["tissue_weight_lb"] += line_weight_lb

        boxes_map = _safe_json_load(leg.boxes_json)
        if isinstance(boxes_map, dict):
            for code, count in boxes_map.items():
                try:
                    qty = int(count)
                except (TypeError, ValueError):
                    continue
                if qty <= 0:
                    continue
                box = box_index_by_code.get(str(code).upper())
                # ``None`` whenever we don't have a usable tare - either
                # the box code is unknown to the SC reference table or
                # the matched row has a NULL ``tare_weight_lb``. The
                # column is ``nullable=False`` in the schema but a
                # future migration or a directly-constructed test row
                # could leave it ``None``; the template guards on
                # ``is not none`` so both paths cleanly omit the
                # per-row weight breakdown instead of rendering "0.00
                # lb/ea". The subtotal still adds 0 for the
                # unknown/unspecified case so ``boxes_weight_lb`` stays
                # consistent with the rendered breakdown.
                tare_weight_lb = (
                    float(box.tare_weight_lb)
                    if box is not None
                    and box.tare_weight_lb is not None
                    else None
                )
                line_weight_lb = (tare_weight_lb or 0.0) * qty
                row["boxes"].append(
                    {
                        "code": str(code),
                        "label": (box.label if box is not None else "") or "",
                        "count": qty,
                        "tare_weight_lb": tare_weight_lb,
                        "line_weight_lb": line_weight_lb,
                    }
                )
                row["boxes_weight_lb"] += line_weight_lb

        consumables_map = _safe_json_load(leg.consumables_json)
        if isinstance(consumables_map, dict):
            for cons_id, qty in consumables_map.items():
                try:
                    cid = int(cons_id)
                    cqty = int(qty)
                except (TypeError, ValueError):
                    continue
                if cqty <= 0:
                    continue
                cons = consumable_index_by_id.get(cid)
                if cons is None:
                    row["consumables"].append(
                        {
                            "name": f"#{cid}",
                            "temp_mode": "",
                            "scope": "",
                            "qty": cqty,
                            "weight_lb": 0.0,
                        }
                    )
                    continue
                weight_lb = (
                    float(cons.weight_lb_per_box or 0.0) * cqty
                )
                row["consumables"].append(
                    {
                        "name": cons.consumable_type,
                        "temp_mode": cons.temp_mode,
                        "scope": cons.scope,
                        "qty": cqty,
                        "weight_lb": weight_lb,
                    }
                )
                row["consumables_weight_lb"] += weight_lb

        row["shipment_weight_lb"] = (
            row["tissue_weight_lb"]
            + row["boxes_weight_lb"]
            + row["consumables_weight_lb"]
        )

        out.append(row)
    return out


_BOOKING_INTAKE_SHIPPER_FIELDS = (
    "name",
    "street",
    "city",
    "state",
    "zip",
    "contact",
    "reference",
    "phone",
    "notes",
)
_BOOKING_INTAKE_CONSIGNEE_FIELDS = _BOOKING_INTAKE_SHIPPER_FIELDS


def _load_booking_intake(session: SCQuoteSession) -> dict[str, Any]:
    """Deserialize ``SCQuoteSession.booking_intake_json`` into a dict.

    Returns an empty dict (``{"pickup_date": "", "delivery_date": "",
    "shipper": {}, "consignee": {}}`` shape) when no intake has been
    submitted yet so the composer/template can `.get()` keys without
    branching.
    """

    raw = _safe_json_load(session.booking_intake_json)
    if not isinstance(raw, dict):
        raw = {}
    shipper = raw.get("shipper") if isinstance(raw.get("shipper"), dict) else {}
    consignee = (
        raw.get("consignee") if isinstance(raw.get("consignee"), dict) else {}
    )
    return {
        "pickup_date": str(raw.get("pickup_date") or ""),
        "delivery_date": str(raw.get("delivery_date") or ""),
        "shipper": {f: str(shipper.get(f) or "") for f in _BOOKING_INTAKE_SHIPPER_FIELDS},
        "consignee": {
            f: str(consignee.get(f) or "")
            for f in _BOOKING_INTAKE_CONSIGNEE_FIELDS
        },
    }


def _booking_intake_has_content(intake: Mapping[str, Any]) -> bool:
    """Return ``True`` when any intake field has been filled in.

    The booking-email templates render the intake block only when this
    returns ``True`` so a never-filled session doesn't ship an empty
    "Booking details" header.
    """

    if intake.get("pickup_date") or intake.get("delivery_date"):
        return True
    for block in ("shipper", "consignee"):
        for value in (intake.get(block) or {}).values():
            if str(value or "").strip():
                return True
    return False


def _parse_booking_intake_form(form: Mapping[str, str]) -> dict[str, Any]:
    """Turn the intake form payload into the JSON shape we persist.

    All values are trimmed strings; date fields are passed through
    verbatim (the ``<input type="date">`` browser widget produces
    ISO ``YYYY-MM-DD`` already). Unknown form keys are ignored so a
    future field addition doesn't require a parser change at every
    call site.
    """

    def _f(key: str) -> str:
        return (form.get(key) or "").strip()

    return {
        "pickup_date": _f("pickup_date"),
        "delivery_date": _f("delivery_date"),
        "shipper": {
            field: _f(f"shipper_{field}")
            for field in _BOOKING_INTAKE_SHIPPER_FIELDS
        },
        "consignee": {
            field: _f(f"consignee_{field}")
            for field in _BOOKING_INTAKE_CONSIGNEE_FIELDS
        },
    }


@science_care_bp.get("/quote/<int:session_id>/email-ops/intake")
@login_required
@sc_user_required
def sc_email_ops_intake(session_id: int):
    """Render the order-level intake form.

    Pre-fills from ``SCQuoteSession.booking_intake_json`` so the user
    can fix typos or finish a partially-filled form without losing
    data. Submitting the form posts to
    :func:`sc_email_ops_intake_submit` which persists the JSON and
    redirects to the booking-email composer.
    """

    session = _load_sc_session_or_404(session_id)
    intake = _load_booking_intake(session)
    return render_template(
        "sc/email_ops_intake.html",
        sc_session=session,
        intake=intake,
    )


@science_care_bp.post("/quote/<int:session_id>/email-ops/intake")
@login_required
@sc_user_required
def sc_email_ops_intake_submit(session_id: int):
    """Persist the order-level intake form and continue to the composer.

    Stores the parsed form as JSON on ``booking_intake_json`` and
    redirects to ``GET /sc/quote/<id>/email-ops`` so the user lands
    on the preview with their answers reflected at the top of the
    body. Idempotent - resubmitting overwrites the prior JSON.
    """

    session = _load_sc_session_or_404(session_id)
    intake = _parse_booking_intake_form(request.form)
    session.booking_intake_json = json.dumps(intake)
    db.session.commit()
    return redirect(
        url_for(
            "science_care.sc_email_ops_for_booking",
            session_id=session.id,
        )
    )


@science_care_bp.get("/quote/<int:session_id>/email-ops")
@login_required
@sc_user_required
def sc_email_ops_for_booking(session_id: int):
    """Render the aggregated multi-leg booking email page.

    Unlike :func:`app.quotes.routes.email_request_form`, this view
    intentionally adds **no** admin / booking fee to the grand total -
    SC multi-leg jobs are billed off the raw cheapest-of total. The
    page generates a ``mailto:operations@freightservices.net`` link
    pre-populated with one block per non-skipped leg, the grand total,
    and the multi-leg reference in the subject. The composer also
    renders the order-level booking intake (shipper / consignee /
    pickup-delivery dates) captured on
    ``/sc/quote/<id>/email-ops/intake`` above the per-leg summary
    when present.
    """

    session = _load_sc_session_or_404(session_id)
    leg_rows = _load_sc_session_legs(session)
    legs = _hydrate_legs_for_display(leg_rows, session=session)
    intake = _load_booking_intake(session)
    return render_template(
        "sc/email_ops_request.html",
        sc_session=session,
        legs=legs,
        grand_total=float(session.grand_total or 0.0),
        user_name=getattr(current_user, "name", "") or "",
        user_email=getattr(current_user, "email", "") or "",
        user_company=getattr(current_user, "company_name", "") or "",
        intake=intake,
        intake_has_content=_booking_intake_has_content(intake),
    )


@science_care_bp.post("/quote/<int:session_id>/email-ops/send")
@login_required
@sc_user_required
def sc_email_ops_send(session_id: int):
    """Send the SC booking email via Postmark (SMTP gateway).

    Renders the same plain-text body shown in the composer plus a
    formatted HTML alternative, calls
    :func:`app.services.mail.send_email` with the ops recipient as the
    ``To`` address and the requesting user as ``Cc``, and persists a
    :class:`~app.models.BookingEmailReceipt` row regardless of
    outcome. The composer's mailto link is unaffected and continues to
    work as an offline fallback even when this endpoint fails.

    Returns a JSON payload describing the outcome so the composer's
    JS can update the status banner. The HTTP status code mirrors
    success / failure for non-JS callers and tests.
    """

    session = _load_sc_session_or_404(session_id)
    leg_rows = _load_sc_session_legs(session)
    legs = _hydrate_legs_for_display(leg_rows, session=session)

    ref_display = session.multi_reference or f"Session {session.id}"
    subject = f"SC Multi-leg Booking Request — {ref_display}"
    to_addr = booking_email_ops_to()
    cc_addr = (getattr(current_user, "email", "") or "").strip() or None

    user_name = getattr(current_user, "name", "") or ""
    user_email = getattr(current_user, "email", "") or ""
    user_company = getattr(current_user, "company_name", "") or ""
    intake = _load_booking_intake(session)
    intake_has_content = _booking_intake_has_content(intake)

    text_body = render_template(
        "sc/emails/booking_request.txt",
        sc_session=session,
        legs=legs,
        grand_total=float(session.grand_total or 0.0),
        user_name=user_name,
        user_email=user_email,
        user_company=user_company,
        intake=intake,
        intake_has_content=intake_has_content,
    )
    html_body = render_template(
        "sc/emails/booking_request.html",
        sc_session=session,
        legs=legs,
        grand_total=float(session.grand_total or 0.0),
        user_name=user_name,
        user_email=user_email,
        user_company=user_company,
        intake=intake,
        intake_has_content=intake_has_content,
    )

    headers: dict[str, str] = {}
    if cc_addr and cc_addr.lower() != to_addr.lower():
        headers["Cc"] = cc_addr
    else:
        # ``Cc`` would duplicate the ``To`` address (e.g. the ops
        # inbox initiating the send for testing); don't bother
        # sending a redundant CC envelope. We still record cc_addr
        # as ``None`` on the receipt so the UI doesn't claim a
        # second recipient.
        cc_addr = None

    # Persist a ``pending`` receipt BEFORE the external send_email call
    # so a server crash, timeout, or worker kill mid-send still leaves
    # an audit row. The final state (``sent`` / ``failed``) is written
    # in a second commit after send_email returns or raises. A stuck
    # ``pending`` row therefore unambiguously means "send_email never
    # returned" - useful when reconciling against the ops inbox.
    receipt = BookingEmailReceipt(
        kind=BOOKING_EMAIL_KIND_SC_MULTI,
        reference=ref_display,
        sender_user_id=getattr(current_user, "id", None),
        to_addr=to_addr,
        cc_addr=cc_addr,
        subject=subject,
        status=BOOKING_EMAIL_STATUS_PENDING,
    )
    db.session.add(receipt)
    db.session.commit()

    try:
        send_email(
            to_addr,
            subject,
            text_body,
            feature="sc_booking_email",
            user=current_user,
            headers=headers or None,
            html_body=html_body,
        )
    except MailRateLimitError as exc:
        receipt = record_booking_email_failure(receipt, str(exc))
        return (
            {
                "status": "failed",
                "message": str(exc),
                "receipt_id": receipt.id,
            },
            429,
        )
    except Exception as exc:  # pragma: no cover - surfaced via tests
        # Roll back FIRST so any subsequent attribute access on the
        # in-memory ``session`` / ``receipt`` objects doesn't trigger
        # a lazy refresh through the now-poisoned transaction. The
        # log message uses the local ``ref_display`` string instead
        # of ``session.id`` for the same reason - expired-after-commit
        # SQLAlchemy attributes refresh via SELECT.
        receipt = record_booking_email_failure(
            receipt, f"{exc.__class__.__name__}: {exc}"
        )
        current_app.logger.exception(
            "SC booking email send failed for ref %s: %s",
            ref_display,
            exc,
        )
        return (
            {
                "status": "failed",
                "message": "Failed to send via Postmark. Use the mail-client fallback below.",
                "receipt_id": receipt.id,
            },
            502,
        )

    receipt.status = BOOKING_EMAIL_STATUS_SENT
    db.session.commit()
    return {
        "status": "sent",
        "sent_at": receipt.sent_at.isoformat(timespec="seconds") + "Z",
        "to_addr": receipt.to_addr,
        "cc_addr": receipt.cc_addr,
        "subject": receipt.subject,
        "receipt_id": receipt.id,
    }


@science_care_bp.get("/quote/lookup")
@login_required
@sc_user_required
def sc_quote_lookup_form() -> str:
    """Render the multi-leg quote lookup form."""

    return render_template("sc/lookup.html")


@science_care_bp.post("/quote/lookup")
@login_required
@sc_user_required
def sc_quote_lookup():
    """Resolve a multi-leg reference to its persisted summary.

    SC-scoped across users: any SC user can look up any multi-leg
    reference. This is intentional - the lookup is how an SC user
    helps a customer find their job by the printed SCMQ number.
    """

    reference, error = _normalize_multi_reference(
        request.form.get("multi_reference")
    )
    if error or not reference:
        flash(error or "Reference is required.", "danger")
        return redirect(url_for("science_care.sc_quote_lookup_form"))

    session = (
        SCQuoteSession.query.filter_by(multi_reference=reference).first()
    )
    if session is None:
        flash(f"No multi-leg quote found for {reference}.", "warning")
        return redirect(url_for("science_care.sc_quote_lookup_form"))

    leg_rows = _load_sc_session_legs(session)
    legs = _hydrate_legs_for_display(leg_rows, session=session)
    return render_template(
        "sc/lookup.html",
        sc_session=session,
        legs=legs,
        grand_total=float(session.grand_total or 0.0),
        looked_up_ref=reference,
    )


# Display order + per-table descriptions for the reference index page.
# Keys must exist in :data:`SC_TABLE_SPECS`.
_SC_TABLE_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("sc_labs", "Lab code → origin ZIP, contact info"),
    (
        "sc_tissue_codes",
        "Per-tissue weight + default box assignment",
    ),
    (
        "sc_box_types",
        "Allowed shipment boxes with dimensions and tare weight",
    ),
    (
        "sc_consumables",
        "Dry-ice / gel-pack weight additions per box",
    ),
    (
        "sc_established_lanes",
        "Pre-negotiated lab-to-lab freight rates",
    ),
    (
        "sc_accessorial_map",
        "Form-field labels → live accessorial names",
    ),
)


@science_care_bp.get("/reference")
@login_required
@sc_admin_required
def sc_reference_index() -> str:
    """Landing page listing the six SC reference tables."""

    tables = [
        (key, SC_TABLE_SPECS[key].label, description)
        for key, description in _SC_TABLE_DESCRIPTIONS
    ]
    return render_template("sc/reference_index.html", tables=tables)


def _resolve_sc_spec_or_404(table: str):
    """Look up an SC ``TableSpec`` or abort 404 if the key is unknown."""

    spec = get_sc_table_spec(table)
    if spec is None:
        abort(404)
    return spec


# Parser → HTML input metadata. Drives the generic per-row edit form for
# every SC reference table except sc_tissue_codes (which has a custom
# parent + capacities form).
_PARSER_INPUT_META: dict[Any, dict[str, Any]] = {
    _parse_required_string: {"type": "text"},
    _parse_optional_string: {"type": "text"},
    _parse_required_float: {"type": "number", "step": "any"},
    _parse_optional_float: {"type": "number", "step": "any"},
    _parse_required_int: {"type": "number", "step": "1"},
    _parse_optional_int: {"type": "number", "step": "1"},
    _parse_bool_flag: {"type": "checkbox"},
    _parse_zipcode: {
        "type": "text",
        "pattern": r"\d{5}",
        "inputmode": "numeric",
        "maxlength": "10",
    },
    _parse_optional_date: {"type": "date"},
}


def _form_field_meta(column: Any, obj: Any | None) -> dict[str, Any]:
    """Build a render-ready dict describing one row-edit form input.

    The TableSpec's ``ColumnSpec.parser`` identity tells us what HTML
    input type to surface. The generic edit form template renders one
    input per column from this metadata.
    """

    meta = dict(_PARSER_INPUT_META.get(column.parser, {"type": "text"}))
    meta["name"] = column.attr
    meta["header"] = column.header
    meta["required"] = bool(column.required)
    value = getattr(obj, column.attr, None) if obj is not None else None
    if meta["type"] == "checkbox":
        meta["checked"] = bool(value)
        meta["value_str"] = ""
    elif value is None:
        meta["value_str"] = ""
    elif meta["type"] == "date":
        meta["value_str"] = value.isoformat() if hasattr(value, "isoformat") else str(value)
    else:
        meta["value_str"] = str(value)
    return meta


def _parse_row_form(spec: Any) -> tuple[dict[str, Any], list[str]]:
    """Parse and validate request form data against ``spec.columns``.

    Returns ``(values, errors)``. ``values`` is a dict of
    ``attr -> parsed_value`` for columns that parsed cleanly. ``errors``
    is a list of human-readable strings keyed back to the column header.
    """

    values: dict[str, Any] = {}
    errors: list[str] = []
    for column in spec.columns:
        if column.parser is _parse_bool_flag:
            raw = request.form.get(column.attr)
            try:
                parsed = _parse_bool_flag(raw) if raw is not None else False
            except ValueError as exc:
                errors.append(f"{column.header}: {exc}")
                continue
            values[column.attr] = parsed
            continue

        raw = request.form.get(column.attr, "")
        try:
            parsed = column.parser(raw)
        except ValueError as exc:
            errors.append(f"{column.header}: {exc}")
            continue
        if column.required and _is_missing(parsed):
            errors.append(f"{column.header}: enter a value")
            continue
        values[column.attr] = parsed
    return values, errors


def _conflict_filter(spec: Any, values: dict[str, Any]):
    """Return a ``filter_by`` kwargs dict for the spec's unique key.

    Returns ``None`` if the spec has no ``unique_attr`` (no uniqueness
    check needed) or any unique attribute is absent from ``values``.
    """

    if not spec.unique_attr:
        return None
    attrs = (
        spec.unique_attr
        if isinstance(spec.unique_attr, (list, tuple))
        else (spec.unique_attr,)
    )
    filters: dict[str, Any] = {"rate_set": RATE_SET_SCIENCE_CARE}
    for attr in attrs:
        if attr == "rate_set":
            continue
        if attr not in values:
            return None
        filters[attr] = values[attr]
    return filters


def _row_label(spec: Any, row: Any) -> str:
    """Human-readable label for a row (used in flash messages)."""

    if spec.unique_attr:
        attrs = (
            spec.unique_attr
            if isinstance(spec.unique_attr, (list, tuple))
            else (spec.unique_attr,)
        )
        parts = [
            str(getattr(row, attr, ""))
            for attr in attrs
            if attr != "rate_set"
        ]
        label = " / ".join(p for p in parts if p)
        if label:
            return label
    return f"#{getattr(row, 'id', '?')}"


def _sc_tissue_capacity_map(tissue_code: str) -> dict[str, int]:
    """Return ``{box_code: pieces_per_box}`` for one SC tissue code."""

    rows = (
        SCTissueBoxCapacity.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE, tissue_code=tissue_code
        ).all()
    )
    return {row.box_code: int(row.pieces_per_box) for row in rows}


def _save_tissue_capacities(
    tissue_code: str, new_caps: dict[str, int]
) -> None:
    """Replace the per-box capacities for ``tissue_code``.

    Wipes the existing rows for this (rate_set, tissue_code) pair and
    inserts a fresh set built from ``new_caps`` (``box_code → qty``).
    Zero-qty entries are dropped so the matrix matches the CSV import's
    "missing row means cannot ship" convention.
    """

    SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code=tissue_code
    ).delete(synchronize_session=False)
    db.session.flush()
    for box_code, qty in new_caps.items():
        if qty <= 0:
            continue
        db.session.add(
            SCTissueBoxCapacity(
                rate_set=RATE_SET_SCIENCE_CARE,
                tissue_code=tissue_code,
                box_code=box_code,
                pieces_per_box=qty,
            )
        )


def _refresh_tissue_default_box(
    tissue: SCTissueCode, caps: dict[str, int] | None = None
) -> None:
    """Recompute the legacy default-box hint from the new capacity map.

    Mirrors the CSV importer's behaviour: the parent row's
    ``default_box_type_code`` + ``pieces_per_box`` track the box with
    the largest capacity so legacy callers (and the existing schema)
    keep returning the same allocation hint they did before. Callers
    that already know the new capacity map can pass it in to skip the
    extra SELECT.
    """

    if caps is None:
        caps = _sc_tissue_capacity_map(tissue.tissue_code)
    if not caps:
        tissue.default_box_type_code = None
        tissue.pieces_per_box = None
        return
    default_box, pieces = max(caps.items(), key=lambda kv: kv[1])
    tissue.default_box_type_code = default_box
    tissue.pieces_per_box = pieces


@science_care_bp.get("/reference/<string:table>")
@login_required
@sc_admin_required
def sc_reference_list(table: str) -> str:
    """List every row in one SC reference table for inline maintenance.

    Mirrors the per-table list pages under ``/admin/<table>`` but is
    scoped to ``rate_set == "science_care"`` so an SC admin can never
    see another tenant's rows.
    """

    spec = _resolve_sc_spec_or_404(table)
    query = spec.model.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
    if spec.order_by is not None:
        order_by = (
            spec.order_by
            if isinstance(spec.order_by, (list, tuple))
            else (spec.order_by,)
        )
        query = query.order_by(*order_by)
    rows = query.all()

    columns = [
        {
            "header": col.header,
            "attr": col.attr,
            "formatter": col.formatter,
        }
        for col in spec.columns
    ]

    extra_capacities_by_row_id: dict[int, dict[str, int]] = {}
    if table == "sc_tissue_codes":
        # The TableSpec's per-box columns (Medium / Large / X-Large /
        # Small Airtray / Airtray) use virtual `_pieces_*` attrs that
        # only exist for CSV header bookkeeping - they aren't mapped on
        # SCTissueCode, so the list template would render the dedicated
        # columns blank. Hydrate them from SCTissueBoxCapacity so each
        # capacity lands in its own column. Capacities tied to box codes
        # outside the five CSV-template columns (e.g. a tenant-specific
        # SCBoxType saved via the per-row edit form) are collected into
        # extra_capacities_by_row_id so the template can surface them in
        # a trailing fallback column - otherwise they'd silently
        # disappear from the page now that the badge column is gone.
        canonical_codes = set(_TISSUE_BOX_COLUMN_TO_CODE.values())
        capacities_by_tissue: dict[str, dict[str, int]] = {}
        for cap in SCTissueBoxCapacity.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all():
            capacities_by_tissue.setdefault(cap.tissue_code, {})[
                cap.box_code
            ] = int(cap.pieces_per_box)
        for row in rows:
            caps = capacities_by_tissue.get(row.tissue_code, {})
            for attr, box_code in _TISSUE_BOX_COLUMN_TO_CODE.items():
                setattr(row, attr, caps.get(box_code))
            extras = {
                code: qty
                for code, qty in caps.items()
                if code not in canonical_codes
            }
            if extras:
                extra_capacities_by_row_id[row.id] = extras

    return render_template(
        "sc/reference_list.html",
        table=table,
        table_label=spec.label,
        columns=columns,
        rows=rows,
        extra_capacities_by_row_id=extra_capacities_by_row_id,
    )


@science_care_bp.route(
    "/reference/<string:table>/new", methods=["GET", "POST"]
)
@login_required
@sc_admin_required
def sc_reference_new(table: str) -> Union[str, Response]:
    """Create one row in an SC reference table."""

    spec = _resolve_sc_spec_or_404(table)
    if table == "sc_tissue_codes":
        return _sc_tissue_form(tissue=None)
    return _sc_generic_form(spec, obj=None)


@science_care_bp.route(
    "/reference/<string:table>/<int:row_id>/edit", methods=["GET", "POST"]
)
@login_required
@sc_admin_required
def sc_reference_edit(table: str, row_id: int) -> Union[str, Response]:
    """Edit one row in an SC reference table."""

    spec = _resolve_sc_spec_or_404(table)
    obj = db.session.get(spec.model, row_id)
    if obj is None or getattr(obj, "rate_set", None) != RATE_SET_SCIENCE_CARE:
        abort(404)
    if table == "sc_tissue_codes":
        return _sc_tissue_form(tissue=obj)
    return _sc_generic_form(spec, obj=obj)


@science_care_bp.post(
    "/reference/<string:table>/<int:row_id>/delete"
)
@login_required
@sc_admin_required
def sc_reference_delete(table: str, row_id: int) -> Response:
    """Delete one row from an SC reference table.

    The SC capacity table links to its parents (tissue, box) by string
    code rather than a FK with cascade delete, so deleting either side
    has to wipe the matching capacity rows manually to keep the matrix
    consistent.
    """

    spec = _resolve_sc_spec_or_404(table)
    obj = db.session.get(spec.model, row_id)
    if obj is None or getattr(obj, "rate_set", None) != RATE_SET_SCIENCE_CARE:
        abort(404)
    label = _row_label(spec, obj)
    if table == "sc_tissue_codes":
        SCTissueBoxCapacity.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE,
            tissue_code=obj.tissue_code,
        ).delete(synchronize_session=False)
    elif table == "sc_box_types":
        # Wipe per-box capacities that referenced this box, then refresh
        # the legacy default_box_type_code hint on every affected tissue
        # so a stale code doesn't survive on the parent row.
        affected_tissues = [
            cap.tissue_code
            for cap in SCTissueBoxCapacity.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE, box_code=obj.code
            ).all()
        ]
        SCTissueBoxCapacity.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE, box_code=obj.code
        ).delete(synchronize_session=False)
        db.session.flush()
        for tissue_code in set(affected_tissues):
            tissue = SCTissueCode.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE, tissue_code=tissue_code
            ).first()
            if tissue is not None:
                _refresh_tissue_default_box(tissue)
    db.session.delete(obj)
    db.session.commit()
    flash(f"{spec.label} row {label} deleted.", "success")
    return redirect(url_for("science_care.sc_reference_list", table=table))


def _sc_generic_form(spec: Any, obj: Any) -> Union[str, Response]:
    """Render and handle the generic single-row form.

    GET: render the form prefilled from ``obj`` (or blank when creating).
    POST: validate + persist; on success redirect back to the list, on
    failure rerender with field-level errors + the user's input.
    """

    errors: list[str] = []
    submitted_values: dict[str, str] = {}
    if request.method == "POST":
        values, errors = _parse_row_form(spec)
        submitted_values = {
            col.attr: request.form.get(col.attr, "") for col in spec.columns
        }
        if not errors:
            conflict_filter = _conflict_filter(spec, values)
            if conflict_filter is not None:
                existing = (
                    spec.model.query.filter_by(**conflict_filter).first()
                )
                if existing is not None and getattr(existing, "id", None) != (
                    getattr(obj, "id", None)
                ):
                    errors.append(
                        f"A {spec.label} row with these key fields already exists."
                    )
            if not errors:
                target = obj or spec.model()
                target.rate_set = RATE_SET_SCIENCE_CARE
                for attr, parsed in values.items():
                    setattr(target, attr, parsed)
                if obj is None:
                    db.session.add(target)
                try:
                    db.session.commit()
                except Exception as exc:  # noqa: BLE001 - surfaced as form error
                    db.session.rollback()
                    errors.append(f"Database error: {exc}")
                else:
                    verb = "updated" if obj is not None else "created"
                    flash(
                        f"{spec.label} row {_row_label(spec, target)} {verb}.",
                        "success",
                    )
                    return redirect(
                        url_for(
                            "science_care.sc_reference_list", table=spec.name
                        )
                    )

    fields = []
    for column in spec.columns:
        meta = _form_field_meta(column, obj)
        if request.method == "POST":
            override = submitted_values.get(column.attr, "")
            if meta["type"] == "checkbox":
                meta["checked"] = bool(request.form.get(column.attr))
            else:
                meta["value_str"] = override
        fields.append(meta)

    status = 400 if request.method == "POST" and errors else 200
    return (
        render_template(
            "sc/reference_form.html",
            table=spec.name,
            table_label=spec.label,
            fields=fields,
            obj=obj,
            errors=errors,
            list_url=url_for(
                "science_care.sc_reference_list", table=spec.name
            ),
        ),
        status,
    )


def _sc_tissue_form(tissue: SCTissueCode | None) -> Union[str, Response]:
    """Render + handle the SC tissue-code form (parent + box capacities).

    Tissue rows carry a per-box capacity matrix. The form shows the
    parent fields plus one ``Pieces / box`` input per existing SCBoxType
    so the SC admin can edit a tissue end-to-end without touching CSVs.
    """

    box_types = (
        SCBoxType.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(SCBoxType.code)
        .all()
    )

    errors: list[str] = []
    submitted: dict[str, str] = {}
    submitted_caps: dict[str, str] = {}

    if request.method == "POST":
        submitted = {
            "tissue_code": request.form.get("tissue_code", "").strip(),
            "description": request.form.get("description", ""),
            "unit_weight_lb": request.form.get("unit_weight_lb", ""),
            "notes": request.form.get("notes", ""),
        }
        for box in box_types:
            submitted_caps[box.code] = request.form.get(
                f"cap_{box.code}", ""
            )

        # Tissue code is the join key for SCTissueBoxCapacity, so let the
        # admin rename it on edit would silently orphan every capacity
        # row. Pin to the stored value when editing.
        if tissue is not None:
            tissue_code = tissue.tissue_code
        else:
            try:
                tissue_code = _parse_required_string(submitted["tissue_code"])
            except ValueError as exc:
                errors.append(f"Tissue Code: {exc}")
                tissue_code = None

        description = _parse_optional_string(submitted["description"])

        try:
            unit_weight = _parse_required_float(submitted["unit_weight_lb"])
        except ValueError as exc:
            errors.append(f"Unit Weight (lb): {exc}")
            unit_weight = None

        notes = _parse_optional_string(submitted["notes"])

        per_box: dict[str, int] = {}
        for box in box_types:
            raw = submitted_caps.get(box.code, "")
            if _is_missing(raw):
                continue
            try:
                qty = _parse_required_int(raw)
            except ValueError as exc:
                errors.append(f"{box.code} pieces / box: {exc}")
                continue
            if qty < 0:
                errors.append(
                    f"{box.code} pieces / box: must be 0 or greater"
                )
                continue
            per_box[box.code] = qty

        if tissue_code is not None and not errors:
            existing = (
                SCTissueCode.query.filter_by(
                    rate_set=RATE_SET_SCIENCE_CARE,
                    tissue_code=tissue_code,
                ).first()
            )
            if existing is not None and (
                tissue is None or existing.id != tissue.id
            ):
                errors.append(
                    f"A tissue code {tissue_code!r} already exists."
                )

        if not errors:
            target = tissue or SCTissueCode()
            target.rate_set = RATE_SET_SCIENCE_CARE
            target.tissue_code = tissue_code
            target.description = description
            target.unit_weight_lb = unit_weight
            target.notes = notes
            if tissue is None:
                db.session.add(target)
            db.session.flush()
            _save_tissue_capacities(target.tissue_code, per_box)
            _refresh_tissue_default_box(target, per_box)
            try:
                db.session.commit()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                errors.append(f"Database error: {exc}")
            else:
                verb = "updated" if tissue is not None else "created"
                flash(
                    f"Tissue code {target.tissue_code} {verb}.",
                    "success",
                )
                return redirect(
                    url_for(
                        "science_care.sc_reference_list",
                        table="sc_tissue_codes",
                    )
                )

    if request.method == "POST":
        values = submitted
        capacities = submitted_caps
    else:
        values = {
            "tissue_code": tissue.tissue_code if tissue else "",
            "description": (tissue.description or "") if tissue else "",
            "unit_weight_lb": (
                str(tissue.unit_weight_lb) if tissue else ""
            ),
            "notes": (tissue.notes or "") if tissue else "",
        }
        cap_map = (
            _sc_tissue_capacity_map(tissue.tissue_code) if tissue else {}
        )
        capacities = {
            box.code: str(cap_map.get(box.code, "")) for box in box_types
        }

    status = 400 if request.method == "POST" and errors else 200
    return (
        render_template(
            "sc/reference_tissue_form.html",
            tissue=tissue,
            values=values,
            capacities=capacities,
            box_types=box_types,
            errors=errors,
            list_url=url_for(
                "science_care.sc_reference_list", table="sc_tissue_codes"
            ),
        ),
        status,
    )


@science_care_bp.route(
    "/reference/<string:table>/upload", methods=["GET", "POST"]
)
@login_required
@sc_admin_required
def sc_upload_csv(table: str) -> Union[str, Response]:
    """Upload a CSV that appends to or replaces an SC reference table.

    Behaviour matches ``app.admin.upload_csv`` (same form, same parser,
    same validation, same audit row), narrowed to SC scope:

    * ``replace`` mode deletes only rows where ``rate_set == "science_care"``
      so other tenants' rows are untouched.
    * Every parsed row's ``rate_set`` is forced to ``"science_care"`` before
      it hits the DB - the CSV's own ``rate_set`` column (if present) is
      ignored on import.
    """

    spec = _resolve_sc_spec_or_404(table)
    form = CSVUploadForm()
    if form.validate_on_submit():
        file_storage = form.file.data
        # sc_tissue_codes carries one capacity row per non-zero box-size
        # column - the generic single-model parser can't emit those, so
        # this branch handles parse + write itself before falling back
        # to the shared path for everything else.
        if table == "sc_tissue_codes":
            try:
                tissues, capacities = parse_sc_tissue_codes_csv(file_storage)
            except (ValueError, pd.errors.EmptyDataError) as exc:
                form.file.errors.append(str(exc))
            else:
                action = form.action.data
                inserted = len(tissues)
                skipped = 0
                try:
                    if action == "replace":
                        replace_sc_tissue_codes(tissues, capacities)
                        message = (
                            f"{spec.label} data replaced with "
                            f"{inserted} row(s)."
                        )
                    else:
                        inserted, skipped = append_sc_tissue_codes(
                            tissues, capacities
                        )
                        message = (
                            f"{spec.label} upload added {inserted} row(s)."
                        )
                        if skipped:
                            message = (
                                f"{spec.label} upload added {inserted} "
                                f"row(s) ({skipped} duplicate row(s) "
                                "skipped)."
                            )

                    record_rate_upload(
                        db.session, spec.name, file_storage.filename
                    )
                    db.session.commit()
                except Exception as exc:  # noqa: BLE001 - re-surfaced on form
                    db.session.rollback()
                    form.file.errors.append(f"Database error: {exc}")
                else:
                    flash(message, "success")
                    return redirect(
                        url_for(
                            "science_care.sc_reference_list", table=table
                        )
                    )
        else:
            try:
                objects = _parse_csv_rows(file_storage, spec)
            except (ValueError, pd.errors.EmptyDataError) as exc:
                form.file.errors.append(str(exc))
            else:
                force_science_care_rate_set(objects)
                action = form.action.data
                inserted = len(objects)
                skipped = 0
                # Wrap the write block in try/rollback so a unique- or FK-
                # constraint failure (or anything else SQLAlchemy raises)
                # doesn't leak an open transaction back to the next request
                # or 500 in the user's face. The same pattern is used by
                # other admin upload paths.
                try:
                    if action == "replace":
                        spec.model.query.filter_by(
                            rate_set=RATE_SET_SCIENCE_CARE
                        ).delete(synchronize_session=False)
                        db.session.flush()
                        db.session.bulk_save_objects(objects)
                        message = (
                            f"{spec.label} data replaced with "
                            f"{inserted} row(s)."
                        )
                    else:
                        if spec.unique_attr:
                            inserted, skipped = save_unique(
                                db.session,
                                spec.model,
                                objects,
                                spec.unique_attr,
                            )
                        else:
                            db.session.bulk_save_objects(objects)
                        message = (
                            f"{spec.label} upload added {inserted} row(s)."
                        )
                        if spec.unique_attr and skipped:
                            message = (
                                f"{spec.label} upload added {inserted} "
                                f"row(s) ({skipped} duplicate row(s) "
                                "skipped)."
                            )

                    record_rate_upload(
                        db.session, spec.name, file_storage.filename
                    )
                    db.session.commit()
                except Exception as exc:  # noqa: BLE001 - re-surfaced on form
                    db.session.rollback()
                    form.file.errors.append(f"Database error: {exc}")
                else:
                    flash(message, "success")
                    return redirect(
                        url_for(
                            "science_care.sc_reference_list", table=table
                        )
                    )

    status = 400 if request.method == "POST" else 200
    return (
        render_template(
            "admin_upload.html",
            form=form,
            table=table,
            table_label=spec.label,
            expected_headers=[col.header for col in spec.columns],
            download_url=url_for(
                "science_care.sc_download_csv", table=table
            ),
            cancel_url=url_for(
                "science_care.sc_reference_list", table=table
            ),
        ),
        status,
    )


@science_care_bp.get("/reference/<string:table>/download")
@login_required
@sc_admin_required
def sc_download_csv(table: str) -> Response:
    """Stream an SC reference table as a CSV template.

    Filtered to ``rate_set == "science_care"`` so an SC admin can never
    see another tenant's rows.
    """

    spec = _resolve_sc_spec_or_404(table)
    if table == "sc_tissue_codes":
        # Custom serializer: one CSV row per tissue, with one column per
        # box-size capacity (zeros for missing rows).
        body = download_sc_tissue_codes_csv()
    else:
        query = spec.model.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        if spec.order_by is not None:
            order_by = (
                spec.order_by
                if isinstance(spec.order_by, (list, tuple))
                else (spec.order_by,)
            )
            query = query.order_by(*order_by)
        rows = query.all()

        output = io.StringIO()
        writer = csv.writer(output)
        headers = [column.header for column in spec.columns]
        writer.writerow(headers)
        for row in rows:
            writer.writerow([column.export(row) for column in spec.columns])
        body = output.getvalue()

    response = Response(body, mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f"attachment; filename={spec.name}_template.csv"
    )
    return response
