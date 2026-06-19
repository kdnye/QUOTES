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

from flask import render_template, request
from flask_login import login_required

from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCAccessorialMap,
    SCBoxType,
    SCLab,
    SCTissueCode,
)
from app.policies import sc_admin_required, sc_user_required

from . import science_care_bp


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


@science_care_bp.get("/quote/tissue-row")
@login_required
@sc_user_required
def sc_tissue_row_partial() -> str:
    """Return a blank tissue-row partial for HTMX append.

    Query parameters:
        leg: 1-based leg index (clamped to ``[1, SC_LEG_COUNT]``).
        i:   row index within the leg's tissue table (clamped to >= 1).
    """

    try:
        leg = int(request.args.get("leg", 1))
    except (TypeError, ValueError):
        leg = 1
    try:
        row_index = int(request.args.get("i", 1))
    except (TypeError, ValueError):
        row_index = 1

    leg = max(1, min(SC_LEG_COUNT, leg))
    row_index = max(1, row_index)

    return render_template(
        "sc/_tissue_row.html", leg=leg, i=row_index, prefill=None
    )


@science_care_bp.get("/quote/tissue-lookup")
@login_required
@sc_user_required
def sc_tissue_lookup_partial() -> str:
    """Return a tissue row partial pre-filled from a tissue code.

    Query parameters:
        leg: 1-based leg index.
        i:   row index within the leg's tissue table.
        code: SC tissue code (case-insensitive lookup).
    """

    leg = max(1, min(SC_LEG_COUNT, int(request.args.get("leg", 1) or 1)))
    row_index = max(1, int(request.args.get("i", 1) or 1))
    code = (request.args.get("code") or "").strip().upper()
    prefill: SCTissueCode | None = None
    if code:
        prefill = (
            SCTissueCode.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE, tissue_code=code
            ).first()
        )

    return render_template(
        "sc/_tissue_row.html", leg=leg, i=row_index, prefill=prefill
    )


@science_care_bp.get("/quote/lab-lookup")
@login_required
@sc_user_required
def sc_lab_lookup_partial() -> str:
    """Return an origin-zip readout partial for one leg.

    Query parameters:
        leg:  1-based leg index (used to scope the swap target).
        code: SC lab code (case-insensitive lookup).
    """

    leg = max(1, min(SC_LEG_COUNT, int(request.args.get("leg", 1) or 1)))
    code = (request.args.get("code") or "").strip().upper()
    lab: SCLab | None = None
    if code:
        lab = (
            SCLab.query.filter_by(
                rate_set=RATE_SET_SCIENCE_CARE,
                lab_code=code,
                is_active=True,
            ).first()
        )
    return render_template("sc/_lab_lookup.html", leg=leg, lab=lab)


@science_care_bp.get("/reference")
@login_required
@sc_admin_required
def sc_reference_index() -> str:
    """Landing page listing the six SC reference tables.

    The actual ``/sc/reference/<table>/download`` and
    ``/sc/reference/<table>/upload`` endpoints land in the follow-up
    CSV-admin PR; this page only renders the table list so the route is
    reachable end-to-end.
    """

    tables = [
        ("sc_labs", "Labs", "Lab code → origin ZIP, contact info"),
        (
            "sc_tissue_codes",
            "Tissue codes",
            "Per-tissue weight + default box assignment",
        ),
        (
            "sc_box_types",
            "Box types",
            "Allowed shipment boxes with dimensions and tare weight",
        ),
        (
            "sc_consumables",
            "Consumables",
            "Dry-ice / gel-pack weight additions per box",
        ),
        (
            "sc_established_lanes",
            "Established lanes",
            "Pre-negotiated lab-to-lab freight rates",
        ),
        (
            "sc_accessorial_map",
            "Accessorial map",
            "Form-field labels → live accessorial names",
        ),
    ]
    return render_template("sc/reference_index.html", tables=tables)
