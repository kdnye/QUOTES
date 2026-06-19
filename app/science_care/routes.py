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
from flask_login import login_required

from app.admin import CSVUploadForm, _parse_csv_rows
from app.models import (
    RATE_SET_SCIENCE_CARE,
    RateUpload,
    SCAccessorialMap,
    SCBoxType,
    SCLab,
    SCTissueCode,
    db,
)
from app.policies import sc_admin_required, sc_user_required
from app.services.science_care_quote import compute_sc_multileg
from scripts.import_air_rates import save_unique

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

    from flask_login import current_user

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

    from flask_login import current_user

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

    return render_template(
        "sc/_tissue_row.html",
        leg=leg,
        i=row_index,
        prefill=prefill,
        code=code,
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

                db.session.add(
                    RateUpload(
                        table_name=spec.name,
                        filename=file_storage.filename,
                    )
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
