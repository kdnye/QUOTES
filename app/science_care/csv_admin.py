"""CSV download / upload specs for the Science Care reference tables.

A parallel, narrower variant of the admin CSV machinery in
``app/admin.py``. The admin endpoints are super-admin only and operate
across all rate-sets; SC admins need to manage SC-only data without
elevated privileges and without touching another tenant's rows. This
module re-uses the admin helpers as read-only imports and never
modifies them, per the SC blueprint's design constraint.

The :data:`SC_TABLE_SPECS` dict maps each table key (used in the URL
path) to a :class:`~app.admin.TableSpec`. Each TableSpec captures the
column headers, attribute parsers, and unique-key tuple used by the
existing ``_parse_csv_rows`` helper and ``save_unique`` utility.

Upload + download views in :mod:`app.science_care.routes` filter every
query and force every parsed row's ``rate_set`` to ``"science_care"`` so
an SC admin can never accidentally read or write another tenant's data,
regardless of what's in their CSV.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Dict

import pandas as pd

# Imports from app.admin are *read-only*. Mutating any of these helpers
# is out of scope for the SC blueprint per the planning doc.
from app.admin import (
    ColumnSpec,
    TableSpec,
    _is_missing,
    _parse_bool_flag,
    _parse_optional_float,
    _parse_optional_int,
    _parse_required_float,
    _parse_required_int,
    _parse_required_string,
    _parse_zipcode,
)
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCAccessorialMap,
    SCBoxType,
    SCConsumable,
    SCEstablishedLane,
    SCLab,
    SCTissueBoxCapacity,
    SCTissueCode,
    db,
)


# --- Custom parsers ----------------------------------------------------------
#
# _parse_optional_float / _parse_optional_int are imported above from
# app.admin - their behavior is identical (check _is_missing, delegate
# to the required variant) and re-implementing them here would invite
# silent drift.
#
# _parse_optional_string is intentionally NOT shared: app.admin's
# version returns "" for a whitespace-only cell, while the SC version
# below returns None so the column stores NULL (the SC schema's
# nullable string columns mean None and "" are semantically different).
# A future refactor could collapse this once admin agrees on the
# nullable semantics.


def _parse_optional_string(value: Any) -> str | None:
    """Return ``value`` as a stripped string, or ``None`` when blank.

    Used for columns that map to nullable ``db.String`` attributes
    (e.g. ``SCLab.address``, ``SCTissueCode.notes``). Whitespace-only
    cells map to ``None`` so the DB stores NULL.
    """

    if _is_missing(value):
        return None
    return str(value).strip() or None


def _parse_optional_date(value: Any) -> date | None:
    """Parse ISO-format ``YYYY-MM-DD`` dates, returning ``None`` when blank.

    Effective dates on :class:`SCEstablishedLane` are nullable so a row
    without an end date stays current indefinitely.
    """

    if _is_missing(value):
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"date must be ISO format YYYY-MM-DD (got {text!r})"
        ) from exc


def _format_date(value: date | None) -> str:
    """Stringify a :class:`datetime.date` for CSV export."""

    return value.isoformat() if value else ""


# --- TableSpec definitions ---------------------------------------------------

#: Ordered mapping of SC reference tables surfaced under /sc/reference.
SC_TABLE_SPECS: Dict[str, TableSpec] = {
    "sc_labs": TableSpec(
        name="sc_labs",
        label="SC Labs",
        model=SCLab,
        list_endpoint="science_care.sc_reference_index",
        unique_attr=("rate_set", "lab_code"),
        order_by=SCLab.lab_code,
        columns=(
            ColumnSpec(
                header="Lab Code",
                attr="lab_code",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Lab Name",
                attr="lab_name",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Origin ZIP",
                attr="origin_zip",
                parser=_parse_zipcode,
            ),
            ColumnSpec(
                header="Address",
                attr="address",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Contact Name",
                attr="contact_name",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Contact Phone",
                attr="contact_phone",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Active",
                attr="is_active",
                parser=_parse_bool_flag,
                required=False,
                formatter=lambda v: "Y" if v else "N",
            ),
        ),
    ),
    # sc_tissue_codes uses a custom import/export path (see
    # parse_sc_tissue_codes_csv / download_sc_tissue_codes_csv) because
    # one CSV row stores BOTH an SCTissueCode and one SCTissueBoxCapacity
    # per non-zero box-size column. The TableSpec here is kept around
    # only so the upload page can announce the expected headers - the
    # routes intercept this table key before calling _parse_csv_rows.
    "sc_tissue_codes": TableSpec(
        name="sc_tissue_codes",
        label="SC Tissue Codes",
        model=SCTissueCode,
        list_endpoint="science_care.sc_reference_index",
        unique_attr=("rate_set", "tissue_code"),
        order_by=SCTissueCode.tissue_code,
        columns=(
            ColumnSpec(
                header="Tissue Code",
                attr="tissue_code",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Description",
                attr="description",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Unit Weight (lb)",
                attr="unit_weight_lb",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Medium",
                attr="_pieces_med",
                parser=_parse_optional_int,
                required=False,
            ),
            ColumnSpec(
                header="Large",
                attr="_pieces_lrg",
                parser=_parse_optional_int,
                required=False,
            ),
            ColumnSpec(
                header="X-Large",
                attr="_pieces_xlg",
                parser=_parse_optional_int,
                required=False,
            ),
            ColumnSpec(
                header="Small Airtray",
                attr="_pieces_small_airtray",
                parser=_parse_optional_int,
                required=False,
            ),
            ColumnSpec(
                header="Airtray",
                attr="_pieces_airtray",
                parser=_parse_optional_int,
                required=False,
            ),
            ColumnSpec(
                header="Notes",
                attr="notes",
                parser=_parse_optional_string,
                required=False,
            ),
        ),
    ),
    "sc_box_types": TableSpec(
        name="sc_box_types",
        label="SC Box Types",
        model=SCBoxType,
        list_endpoint="science_care.sc_reference_index",
        unique_attr=("rate_set", "code"),
        order_by=SCBoxType.code,
        columns=(
            ColumnSpec(
                header="Code", attr="code", parser=_parse_required_string
            ),
            ColumnSpec(
                header="Label",
                attr="label",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Length (in)",
                attr="length_in",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Width (in)",
                attr="width_in",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Height (in)",
                attr="height_in",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Tare Weight (lb)",
                attr="tare_weight_lb",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Max Payload (lb)",
                attr="max_payload_lb",
                parser=_parse_optional_float,
                required=False,
            ),
        ),
    ),
    "sc_consumables": TableSpec(
        name="sc_consumables",
        label="SC Consumables",
        model=SCConsumable,
        list_endpoint="science_care.sc_reference_index",
        unique_attr=(
            "rate_set",
            "consumable_type",
            "temp_mode",
            "scope",
        ),
        order_by=(
            SCConsumable.consumable_type,
            SCConsumable.temp_mode,
            SCConsumable.scope,
        ),
        columns=(
            ColumnSpec(
                header="Consumable Type",
                attr="consumable_type",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Temp Mode",
                attr="temp_mode",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Scope",
                attr="scope",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Weight Per Box (lb)",
                attr="weight_lb_per_box",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Notes",
                attr="notes",
                parser=_parse_optional_string,
                required=False,
            ),
        ),
    ),
    "sc_established_lanes": TableSpec(
        name="sc_established_lanes",
        label="SC Established Lanes",
        model=SCEstablishedLane,
        list_endpoint="science_care.sc_reference_index",
        unique_attr=(
            "rate_set",
            "origin_zip",
            "dest_zip",
            "service_type",
        ),
        order_by=(
            SCEstablishedLane.origin_zip,
            SCEstablishedLane.dest_zip,
            SCEstablishedLane.service_type,
        ),
        columns=(
            ColumnSpec(
                header="Origin ZIP",
                attr="origin_zip",
                parser=_parse_zipcode,
            ),
            ColumnSpec(
                header="Dest ZIP",
                attr="dest_zip",
                parser=_parse_zipcode,
            ),
            ColumnSpec(
                header="Service Type",
                attr="service_type",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Rate",
                attr="rate",
                parser=_parse_required_float,
            ),
            ColumnSpec(
                header="Effective From",
                attr="effective_from",
                parser=_parse_optional_date,
                required=False,
                formatter=_format_date,
            ),
            ColumnSpec(
                header="Effective To",
                attr="effective_to",
                parser=_parse_optional_date,
                required=False,
                formatter=_format_date,
            ),
        ),
    ),
    "sc_accessorial_map": TableSpec(
        name="sc_accessorial_map",
        label="SC Accessorial Map",
        model=SCAccessorialMap,
        list_endpoint="science_care.sc_reference_index",
        unique_attr=("rate_set", "form_field"),
        order_by=SCAccessorialMap.form_field,
        columns=(
            ColumnSpec(
                header="Form Field",
                attr="form_field",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Display Label",
                attr="display_label",
                parser=_parse_required_string,
            ),
            ColumnSpec(
                header="Accessorial Name",
                attr="accessorial_name",
                parser=_parse_required_string,
            ),
        ),
    ),
}


def get_sc_table_spec(table: str) -> TableSpec | None:
    """Return the :class:`TableSpec` for ``table`` or ``None`` if unknown."""

    return SC_TABLE_SPECS.get(table)


def force_science_care_rate_set(rows: list[Any]) -> None:
    """Stamp ``rate_set = "science_care"`` on every parsed row.

    The CSV format does not surface ``rate_set`` (the SC admin should
    not be able to write into another tenant's slice of the table), so
    this helper is called after :func:`app.admin._parse_csv_rows` to
    fill it in before the rows hit the database.
    """

    for row in rows:
        row.rate_set = RATE_SET_SCIENCE_CARE


# --- Tissue-code custom CSV path --------------------------------------------
#
# The tissue-code CSV stores both an SCTissueCode (the parent row: code,
# description, weight, notes) and zero-or-more SCTissueBoxCapacity rows
# (one per box-size column with a non-zero qty). The generic TableSpec
# pipeline can only emit one model per row, so the SC routes intercept
# this table key and call the helpers below instead.

# Maps each CSV box-size header to the box code it refers to. The header
# string is the customer's column label (see the spreadsheet template).
# Unicode dashes used in the source template are normalized to ASCII in
# _normalize_box_header() before lookup.
BOX_HEADER_TO_CODE: dict[str, str] = {
    "medium": "MED",
    "large": "LRG",
    "x-large": "XLG",
    "xlarge": "XLG",
    "small airtray": "SMALL_AIRTRAY",
    "smallairtray": "SMALL_AIRTRAY",
    "airtray": "AIRTRAY",
    "airtray (box05)": "AIRTRAY",
}

# Headers we drop into BOX_HEADER_TO_CODE keys at parse time. Kept here
# so the upload page can advertise the same list back to the SC admin.
TISSUE_BOX_CAPACITY_HEADERS: tuple[str, ...] = (
    "Medium",
    "Large",
    "X-Large",
    "Small Airtray",
    "Airtray",
)

# Headers shown to the SC admin on the upload page + emitted on download.
TISSUE_CSV_HEADERS: tuple[str, ...] = (
    "Tissue Code",
    "Description",
    "Unit Weight (lb)",
) + TISSUE_BOX_CAPACITY_HEADERS + ("Notes",)


def _normalize_box_header(header: str) -> str:
    """Normalize a box-capacity column header for case/dash-insensitive lookup."""

    # The customer template uses U+2010 HYPHEN ("X‐Large") in some
    # generations; normalise to ASCII hyphen so the lookup succeeds
    # regardless of source-file dialect.
    return (
        str(header)
        .replace("‐", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("–", "-")
        .replace("—", "-")
        .strip()
        .lower()
    )


def parse_sc_tissue_codes_csv(
    file_storage: Any,
) -> tuple[list[SCTissueCode], list[SCTissueBoxCapacity]]:
    """Parse an SC tissue-codes CSV into parent + capacity rows.

    The expected header layout matches the customer template:

        Tissue Code, Description, Unit Weight (lb), Medium, Large,
        X-Large, Small Airtray, Airtray, Notes

    For each data row, one :class:`SCTissueCode` is produced from the
    code/description/weight/notes columns, and one
    :class:`SCTissueBoxCapacity` is produced for each non-zero box-size
    cell. Zero or blank cells mean "this box cannot ship this tissue"
    and yield no capacity row.

    Raises:
        ValueError: When required columns are missing, the file is empty,
            or any cell fails to parse. The message lists the row number
            and column so the SC admin can fix the CSV.
    """

    file_storage.stream.seek(0)
    df = pd.read_csv(file_storage)
    df.columns = [str(col).lstrip("﻿").strip() for col in df.columns]

    required_meta = {"Tissue Code", "Description", "Unit Weight (lb)"}
    missing = required_meta - set(df.columns)
    if missing:
        raise ValueError(
            "CSV must include columns: "
            + ", ".join(sorted(required_meta)) + "."
        )

    # Build {original_header: box_code} for every header that maps to a
    # known box. Unknown box columns are ignored - they're either typos
    # or a box code the SC tenant hasn't created yet.
    header_to_box: dict[str, str] = {}
    for header in df.columns:
        key = _normalize_box_header(header)
        if key in BOX_HEADER_TO_CODE:
            header_to_box[header] = BOX_HEADER_TO_CODE[key]

    df = df.replace({pd.NA: None})
    tissues: list[SCTissueCode] = []
    capacities: list[SCTissueBoxCapacity] = []
    errors: list[str] = []
    seen_codes: set[str] = set()

    for row_index, row in enumerate(df.to_dict(orient="records"), start=2):
        # Skip fully-blank rows (saves the SC admin from a confusing
        # "enter a value" error for trailing whitespace lines).
        if all(_is_missing(row.get(col)) for col in df.columns):
            continue

        row_errors: list[str] = []
        try:
            code = _parse_required_string(row.get("Tissue Code"))
        except ValueError as exc:
            row_errors.append(f"Tissue Code: {exc}")
            code = None  # type: ignore[assignment]

        description = _parse_optional_string(row.get("Description"))

        try:
            weight = _parse_required_float(row.get("Unit Weight (lb)"))
        except ValueError as exc:
            row_errors.append(f"Unit Weight (lb): {exc}")
            weight = None  # type: ignore[assignment]

        notes = _parse_optional_string(row.get("Notes")) if "Notes" in df.columns else None

        # Pieces per box for every recognised box column. Blank → 0
        # (meaning "this box cannot ship this tissue", same as a 0 in
        # the customer template). Non-integer values surface as a row
        # error so the SC admin can spot the typo.
        per_box: dict[str, int] = {}
        for header, box_code in header_to_box.items():
            raw = row.get(header)
            if _is_missing(raw):
                continue
            try:
                qty = _parse_required_int(raw)
            except ValueError as exc:
                row_errors.append(f"{header}: {exc}")
                continue
            if qty < 0:
                row_errors.append(
                    f"{header}: pieces per box must be >= 0"
                )
                continue
            if qty > 0:
                per_box[box_code] = qty

        if row_errors:
            errors.append(f"Row {row_index}: {'; '.join(row_errors)}")
            continue

        if code in seen_codes:
            errors.append(
                f"Row {row_index}: duplicate tissue code {code!r}"
            )
            continue
        seen_codes.add(code)

        # Derive default_box_type_code + pieces_per_box from the per-box
        # map so legacy callers (and the existing model schema) keep
        # working. The default mirrors the new allocator's preference:
        # smallest box-count, ties broken by smaller interior volume -
        # but here we don't have an order, so just pick the largest
        # capacity (which IS the smallest box count for qty == 1).
        default_box = None
        pieces_per_box = None
        if per_box:
            default_box, pieces_per_box = max(
                per_box.items(), key=lambda kv: kv[1]
            )

        tissue = SCTissueCode(
            tissue_code=code,
            description=description,
            unit_weight_lb=weight,
            default_box_type_code=default_box,
            pieces_per_box=pieces_per_box,
            notes=notes,
        )
        tissues.append(tissue)

        for box_code, qty in per_box.items():
            capacities.append(
                SCTissueBoxCapacity(
                    tissue_code=code,
                    box_code=box_code,
                    pieces_per_box=qty,
                )
            )

    if errors:
        raise ValueError(" ".join(errors))
    if not tissues:
        raise ValueError("No data rows found in the CSV file.")
    return tissues, capacities


def download_sc_tissue_codes_csv() -> str:
    """Serialize every science-care tissue code as the customer template.

    Box capacity columns appear in the canonical order
    (Medium, Large, X-Large, Small Airtray, Airtray). Tissues with no
    capacity rows render zeros across all five columns - exactly what
    the customer's spreadsheet uses to signal "cannot ship in any box".
    """

    tissues = (
        SCTissueCode.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .order_by(SCTissueCode.tissue_code)
        .all()
    )
    caps = (
        SCTissueBoxCapacity.query.filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .all()
    )
    cap_index: dict[tuple[str, str], int] = {
        (c.tissue_code, c.box_code): int(c.pieces_per_box or 0)
        for c in caps
    }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(TISSUE_CSV_HEADERS)

    # Header → box code mapping for the five capacity columns, derived
    # from BOX_HEADER_TO_CODE so adding a column keeps the export and
    # import in sync.
    capacity_columns: list[tuple[str, str]] = [
        (h, BOX_HEADER_TO_CODE[_normalize_box_header(h)])
        for h in TISSUE_BOX_CAPACITY_HEADERS
    ]

    for tissue in tissues:
        row = [
            tissue.tissue_code,
            tissue.description or "",
            tissue.unit_weight_lb,
        ]
        for _header, box_code in capacity_columns:
            row.append(cap_index.get((tissue.tissue_code, box_code), 0))
        row.append(tissue.notes or "")
        writer.writerow(row)

    return output.getvalue()


def replace_sc_tissue_codes(
    tissues: list[SCTissueCode], capacities: list[SCTissueBoxCapacity]
) -> None:
    """Truncate + reload both SC tissue tables for the SC rate-set.

    Wraps the two-table replace in a single flush so a constraint
    failure rolls back both halves. The caller commits the surrounding
    audit row.
    """

    SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).delete(synchronize_session=False)
    SCTissueCode.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).delete(synchronize_session=False)
    db.session.flush()
    for tissue in tissues:
        tissue.rate_set = RATE_SET_SCIENCE_CARE
    for cap in capacities:
        cap.rate_set = RATE_SET_SCIENCE_CARE
    db.session.bulk_save_objects(tissues)
    db.session.bulk_save_objects(capacities)


def append_sc_tissue_codes(
    tissues: list[SCTissueCode], capacities: list[SCTissueBoxCapacity]
) -> tuple[int, int]:
    """Upsert tissue + capacity rows by tissue code, returning (added, skipped).

    A duplicate tissue code in the CSV that already exists in the DB
    counts as skipped (no overwrite) - matches the behaviour of
    :func:`app.services.bulk_import.save_unique` used by the other SC
    tables. New tissue codes get their parent row plus every capacity
    row inserted in one flush.
    """

    existing_codes = {
        code for (code,) in db.session.query(SCTissueCode.tissue_code)
        .filter_by(rate_set=RATE_SET_SCIENCE_CARE)
        .all()
    }
    fresh_tissues = [
        t for t in tissues if t.tissue_code not in existing_codes
    ]
    fresh_codes = {t.tissue_code for t in fresh_tissues}
    fresh_caps = [c for c in capacities if c.tissue_code in fresh_codes]
    for tissue in fresh_tissues:
        tissue.rate_set = RATE_SET_SCIENCE_CARE
    for cap in fresh_caps:
        cap.rate_set = RATE_SET_SCIENCE_CARE
    db.session.bulk_save_objects(fresh_tissues)
    db.session.bulk_save_objects(fresh_caps)
    return len(fresh_tissues), len(tissues) - len(fresh_tissues)
