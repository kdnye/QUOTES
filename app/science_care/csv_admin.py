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

from datetime import date
from typing import Any, Dict

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
    SCTissueCode,
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
                header="Default Box Type",
                attr="default_box_type_code",
                parser=_parse_optional_string,
                required=False,
            ),
            ColumnSpec(
                header="Pieces Per Box",
                attr="pieces_per_box",
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
