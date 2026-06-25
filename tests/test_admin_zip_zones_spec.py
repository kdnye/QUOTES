"""Cross-check that the ZipZone admin TableSpec mirrors the model's

uniqueness contract. The model has
``UniqueConstraint("rate_set", "zipcode")`` (so the same ZIP can carry
different rows under ``default`` vs ``scicr``). Before this PR the
admin TableSpec used ``unique_attr="zipcode"`` only, which meant a
bulk CSV upload would de-duplicate by ZIP alone and silently clobber
per-customer overrides.

These tests pin the contract so a future refactor can't drop the
``rate_set`` column or shrink the unique key without a test failing.
"""

from __future__ import annotations

from app.admin import TABLE_SPECS


def test_zip_zones_spec_includes_rate_set_column() -> None:
    spec = TABLE_SPECS["zip_zones"]
    headers = [col.header for col in spec.columns]
    attrs = [col.attr for col in spec.columns]
    assert "Rate Set" in headers, (
        f"Admin CSV must surface the Rate Set column; got headers={headers}"
    )
    assert "rate_set" in attrs, (
        f"Admin CSV column must map to ZipZone.rate_set; got attrs={attrs}"
    )


def test_zip_zones_spec_unique_attr_matches_model_constraint() -> None:
    """``unique_attr`` MUST be ``("rate_set", "zipcode")`` — same composite
    key the model's ``UniqueConstraint`` enforces. A scalar ``"zipcode"``
    would dedupe by ZIP alone and clobber per-customer overrides during
    a bulk import.
    """

    spec = TABLE_SPECS["zip_zones"]
    assert spec.unique_attr == ("rate_set", "zipcode"), (
        f"unique_attr must match ZipZone's (rate_set, zipcode) "
        f"UniqueConstraint; got {spec.unique_attr!r}"
    )


def test_zip_zones_rate_set_column_uses_rate_set_parser() -> None:
    """Same parser as the other rate-scoped tables — keeps the ``scicr``
    / ``default`` / customer rate-set vocabulary consistent across forms
    and CSVs and lets new rate sets be created via upload."""

    spec = TABLE_SPECS["zip_zones"]
    rate_set_col = next(col for col in spec.columns if col.attr == "rate_set")
    # Smoke: the parser should at least round-trip the canonical value.
    assert rate_set_col.parser("default") == "default"
    assert rate_set_col.parser("scicr") == "scicr"
    # Trims + lowercases so admins typing "DEFAULT" or " scicr " still work.
    assert rate_set_col.parser("DEFAULT") == "default"
    assert rate_set_col.parser(" scicr ") == "scicr"
