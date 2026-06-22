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
from typing import Union

import pandas as pd
from flask import (
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.admin import CSVUploadForm, _parse_csv_rows
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCAccessorialMap,
    SCBoxType,
    SCConsumable,
    SCLab,
    SCTissueCode,
    SCUserLabSlot,
    db,
)
from app.policies import sc_admin_required, sc_user_required
from app.services.science_care_quote import (
    _collect_tissue_rows,
    allocate_boxes,
    compute_sc_multileg,
)
from app.services.bulk_import import record_rate_upload, save_unique

from . import science_care_bp
from .csv_admin import (
    SC_TABLE_SPECS,
    force_science_care_rate_set,
    get_sc_table_spec,
)


# Form field → friendly accessorial labels used when the SC tenant has
# not (yet) populated SCAccessorialMap rows.
_FALLBACK_ACCESSORIAL_LABELS = {
    "J3": "4 Hour Window",
    "J4": "Specific PickUp Time",
    "J5": "Delivery After Hours",
    "J7": "Two Man Delivery",
    "J8": "Liftgate Delivery",
}

# Number of shipment legs supported by the multi-lab form.
SC_LEG_COUNT = 7


def _accessorial_labels() -> list[tuple[str, str]]:
    """Return ``(form_field, display_label)`` pairs for the form.

    Reads :class:`app.models.SCAccessorialMap` for the science-care
    rate-set first; falls back to the hard-coded labels so a freshly
    migrated database still renders a usable form.
    """

    rows = (
        SCAccessorialMap.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(SCAccessorialMap.form_field)
        .all()
    )
    if rows:
        return [(row.form_field, row.display_label) for row in rows]
    return [
        (field, label)
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
    """Render the empty seven-leg multi-lab quote form."""

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
    )


@science_care_bp.post("/quote/calculate")
@login_required
@sc_user_required
def sc_quote_calculate() -> str:
    """HTMX endpoint: run the multi-leg orchestration and swap a partial.

    Returns the ``sc/_results_partial.html`` fragment for HTMX swap.
    Non-HTMX callers (the standard form ``POST /sc/quote`` endpoint)
    can use the same result context.
    """

    context = compute_sc_multileg(
        request.form, current_user, request.remote_addr
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

    context = compute_sc_multileg(
        request.form, current_user, request.remote_addr
    )
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

    tissue_row_html = render_template(
        "sc/_tissue_row.html",
        leg=leg,
        i=row_index,
        prefill=prefill,
        code=code,
    )

    # OOB recompute of the leg's box counts. Mirrors the qty trigger in
    # /sc/quote/leg/<n>/box-counts but here it's piggy-backed on the
    # tissue-code response so swapping a code (e.g. ARM01 -> PELV03)
    # updates the Boxes section in the same round-trip. `request.args`
    # carries the full leg body via the input's hx-include.
    tissue_index = {
        t.tissue_code: t
        for t in SCTissueCode.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    # The freshly-typed code may not yet be reflected in request.args
    # because the user is still editing the input. Splice it in so the
    # OOB box allocation reflects what the visible tissue row will show
    # after the swap completes.
    args = request.args.copy()
    if prefill:
        args[f"tissue_code_{leg}_{row_index}"] = prefill.tissue_code
    box_types = _box_type_choices()
    box_index = {b.code: b for b in box_types}
    tissue_rows = _collect_tissue_rows(args, leg)
    _, _, auto_boxes_by_type, _, _ = allocate_boxes(
        tissue_rows, tissue_index, box_index
    )
    box_values = _resolve_box_values(
        args, leg, box_types, auto_boxes_by_type
    )
    box_counts_html = render_template(
        "sc/_box_count_inputs.html",
        leg=leg,
        box_types=box_types,
        box_values=box_values,
        oob=True,
    )
    return tissue_row_html + box_counts_html


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

    tissue_index = {
        t.tissue_code: t
        for t in SCTissueCode.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    }
    box_types = _box_type_choices()
    box_index = {b.code: b for b in box_types}

    tissue_rows = _collect_tissue_rows(request.form, leg)
    _, _, auto_boxes_by_type, _, _ = allocate_boxes(
        tissue_rows, tissue_index, box_index
    )

    box_values = _resolve_box_values(
        request.form, leg, box_types, auto_boxes_by_type
    )

    return render_template(
        "sc/_box_count_inputs.html",
        leg=leg,
        box_types=box_types,
        box_values=box_values,
    )


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
                return redirect(url_for(spec.list_endpoint))

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
            cancel_url=url_for(spec.list_endpoint),
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

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f"attachment; filename={spec.name}_template.csv"
    )
    return response
