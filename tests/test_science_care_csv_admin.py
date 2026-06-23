"""CSV download / upload tests for the Science Care reference tables.

Covers:
* Auth gating (non-SC user 403, plain SC user 403, SC admin 200).
* Download produces the expected headers for every table.
* Upload round-trips a known CSV into the database.
* Replace mode only deletes the SC tenant's rows.
* The CSV's ``rate_set`` column is ignored - rows are stamped to
  ``science_care`` regardless.
"""

from __future__ import annotations

import io
from typing import Iterable

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCAccessorialMap,
    SCBoxType,
    SCLab,
    SCTissueBoxCapacity,
    SCTissueCode,
    User,
    db,
)
from app.science_care.csv_admin import SC_TABLE_SPECS


class TestSCCsvAdminConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCCsvAdminConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCCsvAdminConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def _make_user(
    email: str, rate_set: str, is_sc_admin: bool = False
) -> User:
    user = User(
        email=email,
        name=email,
        password_hash="x",
        rate_set=rate_set,
        is_sc_admin=is_sc_admin,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _login(client: FlaskClient, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _csv_bytes(lines: Iterable[str]) -> io.BytesIO:
    buf = io.BytesIO()
    buf.write(("\n".join(lines) + "\n").encode("utf-8"))
    buf.seek(0)
    return buf


def test_download_blocked_for_non_sc_user(app: Flask) -> None:
    user = _make_user("nosc@example.com", rate_set="default")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_labs/download")
    assert response.status_code == 403


def test_download_blocked_for_plain_sc_user(app: Flask) -> None:
    user = _make_user(
        "plain@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_labs/download")
    assert response.status_code == 403


def test_download_returns_csv_with_expected_headers(app: Flask) -> None:
    user = _make_user(
        "admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    db.session.add(
        SCLab(
            lab_code="SCCA",
            lab_name="Tucson",
            origin_zip="85705",
            address="1 Lab Way",
            is_active=True,
        )
    )
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_labs/download")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    body = response.get_data(as_text=True)
    spec = SC_TABLE_SPECS["sc_labs"]
    expected_headers = ",".join(col.header for col in spec.columns)
    assert body.splitlines()[0] == expected_headers
    assert "SCCA" in body
    assert "85705" in body


def test_download_unknown_table_404(app: Flask) -> None:
    user = _make_user(
        "admin2@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/not_a_table/download")
    assert response.status_code == 404


def test_download_only_returns_science_care_rows(app: Flask) -> None:
    # An SCLab row stamped to another tenant must not appear in the
    # SC admin's download.
    user = _make_user(
        "admin3@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    db.session.add_all(
        [
            SCLab(lab_code="OURS", origin_zip="85705"),
            SCLab(
                lab_code="THEIRS",
                origin_zip="00501",
                rate_set="other_tenant",
            ),
        ]
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_labs/download")
    body = response.get_data(as_text=True)
    assert "OURS" in body
    assert "THEIRS" not in body


def test_upload_round_trip_appends_rows(app: Flask) -> None:
    user = _make_user(
        "uadmin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _csv_bytes(
        [
            "Code,Label,Length (in),Width (in),Height (in),Tare Weight (lb),Max Payload (lb)",
            "MED,Medium 20x15x18,20,15,18,4,",
            "LRG,Large 32x18x20,32,18,20,8,",
        ]
    )
    response = client.post(
        "/sc/reference/sc_box_types/upload",
        data={
            "file": (csv, "sc_box_types.csv"),
            "action": "add",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302  # redirect on success
    rows = SCBoxType.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).order_by(SCBoxType.code).all()
    # SMALL_AIRTRAY is seeded by the SC tissue-box-capacity migration,
    # so the upload's two new rows land alongside it.
    assert sorted(r.code for r in rows) == ["LRG", "MED", "SMALL_AIRTRAY"]
    assert all(r.rate_set == RATE_SET_SCIENCE_CARE for r in rows)


def test_upload_replace_only_clears_science_care_rows(app: Flask) -> None:
    user = _make_user(
        "radmin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    db.session.add_all(
        [
            SCBoxType(
                code="OLD",
                label="Old",
                length_in=1,
                width_in=1,
                height_in=1,
                tare_weight_lb=0,
            ),
            SCBoxType(
                code="KEEP",
                label="Keep",
                length_in=1,
                width_in=1,
                height_in=1,
                tare_weight_lb=0,
                rate_set="other_tenant",
            ),
        ]
    )
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    csv = _csv_bytes(
        [
            "Code,Label,Length (in),Width (in),Height (in),Tare Weight (lb),Max Payload (lb)",
            "NEW,New,2,2,2,0.5,",
        ]
    )
    response = client.post(
        "/sc/reference/sc_box_types/upload",
        data={
            "file": (csv, "sc_box_types.csv"),
            "action": "replace",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302

    # Other tenant's row survives; SC tenant has only the new row.
    keep_rows = SCBoxType.query.filter_by(rate_set="other_tenant").all()
    assert [r.code for r in keep_rows] == ["KEEP"]
    sc_rows = SCBoxType.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).all()
    assert [r.code for r in sc_rows] == ["NEW"]


def test_upload_blocked_for_plain_sc_user(app: Flask) -> None:
    user = _make_user(
        "plain-upload@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_labs/upload")
    assert response.status_code == 403


def test_upload_empty_optional_cells_persist_as_null(app: Flask) -> None:
    # Regression: an empty optional cell in the CSV must land as NULL
    # in the database, not as the literal string "nan" (pandas decodes
    # blank cells to float NaN, which slips past `value is None`).
    user = _make_user(
        "null-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _csv_bytes(
        [
            "Lab Code,Lab Name,Origin ZIP,Address,Contact Name,Contact Phone,Active",
            "SCCA,,85705,,,,Y",
        ]
    )
    response = client.post(
        "/sc/reference/sc_labs/upload",
        data={"file": (csv, "labs.csv"), "action": "add"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    row = SCLab.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, lab_code="SCCA"
    ).one()
    assert row.lab_name is None
    assert row.address is None
    assert row.contact_name is None
    assert row.contact_phone is None


def test_accessorial_map_csv_round_trip(app: Flask) -> None:
    user = _make_user(
        "acc-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _csv_bytes(
        [
            "Form Field,Display Label,Accessorial Name",
            "J3,4 Hour Window,PickUp 4 Hour Window (e.g 10:00-14:00)",
            "J8,Liftgate Required,Liftgate Delivery",
        ]
    )
    response = client.post(
        "/sc/reference/sc_accessorial_map/upload",
        data={"file": (csv, "map.csv"), "action": "add"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    rows = SCAccessorialMap.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).order_by(SCAccessorialMap.form_field).all()
    assert [r.form_field for r in rows] == ["J3", "J8"]
    assert rows[0].accessorial_name == "PickUp 4 Hour Window (e.g 10:00-14:00)"


# --- Tissue codes with per-box capacity columns -----------------------------


def _tissue_csv(*rows: str) -> io.BytesIO:
    return _csv_bytes(
        [
            "Tissue Code,Description,Unit Weight (lb),Medium,Large,X-Large,Small Airtray,Airtray,Notes",
            *rows,
        ]
    )


def test_tissue_codes_replace_loads_capacity_rows(app: Flask) -> None:
    # Replace mode wipes both sc_tissue_codes and sc_tissue_box_capacity
    # for the SC tenant, then writes the new parent + capacity rows in
    # one transaction.
    user = _make_user(
        "tissue-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _tissue_csv(
        "ARM01,Arm Whole,12,0,7,10,0,0,",
        "CADV02,Embalmed Cadaver,300,0,0,0,0,1,",
    )
    response = client.post(
        "/sc/reference/sc_tissue_codes/upload",
        data={"file": (csv, "tissue.csv"), "action": "replace"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302, response.get_data(as_text=True)

    tissues = {
        t.tissue_code: t
        for t in SCTissueCode.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        )
    }
    assert set(tissues) == {"ARM01", "CADV02"}
    assert tissues["ARM01"].unit_weight_lb == pytest.approx(12.0)
    # ARM01 capacity rows: only the non-zero columns become rows.
    arm01 = SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code="ARM01"
    ).all()
    assert {(r.box_code, r.pieces_per_box) for r in arm01} == {
        ("LRG", 7), ("XLG", 10)
    }
    cadv02 = SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code="CADV02"
    ).all()
    assert {(r.box_code, r.pieces_per_box) for r in cadv02} == {
        ("AIRTRAY", 1)
    }


def test_tissue_codes_replace_does_not_touch_other_tenant(
    app: Flask,
) -> None:
    # Another tenant's tissue + capacity rows survive an SC replace.
    db.session.add_all(
        [
            SCTissueCode(
                tissue_code="OTHER01",
                description="Other tenant",
                unit_weight_lb=1.0,
                rate_set="other_tenant",
            ),
            SCTissueBoxCapacity(
                tissue_code="OTHER01",
                box_code="MED",
                pieces_per_box=5,
                rate_set="other_tenant",
            ),
        ]
    )
    db.session.commit()
    user = _make_user(
        "tenant-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _tissue_csv("ARM01,Arm Whole,12,0,7,10,0,0,")
    response = client.post(
        "/sc/reference/sc_tissue_codes/upload",
        data={"file": (csv, "tissue.csv"), "action": "replace"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    other = SCTissueCode.query.filter_by(
        rate_set="other_tenant", tissue_code="OTHER01"
    ).one()
    assert other.unit_weight_lb == pytest.approx(1.0)
    other_caps = SCTissueBoxCapacity.query.filter_by(
        rate_set="other_tenant", tissue_code="OTHER01"
    ).all()
    assert len(other_caps) == 1


def test_tissue_codes_append_skips_duplicates(app: Flask) -> None:
    # Append mode preserves existing tissue codes and only inserts
    # tissues whose code is brand new.
    db.session.add_all(
        [
            SCTissueCode(
                tissue_code="ARM01",
                description="Existing arm",
                unit_weight_lb=12.0,
            ),
            SCTissueBoxCapacity(
                tissue_code="ARM01",
                box_code="XLG",
                pieces_per_box=10,
            ),
        ]
    )
    db.session.commit()
    user = _make_user(
        "dup-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _tissue_csv(
        # Duplicate of ARM01: skipped.
        "ARM01,Arm Whole replacement,99,0,0,0,0,0,",
        # Brand new: inserted with its capacity rows.
        "ARM02,Mid-Humerus,5,6,10,15,0,0,",
    )
    response = client.post(
        "/sc/reference/sc_tissue_codes/upload",
        data={"file": (csv, "tissue.csv"), "action": "add"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    arm01 = SCTissueCode.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code="ARM01"
    ).one()
    # Original record stays - description must not be overwritten.
    assert arm01.description == "Existing arm"
    arm01_caps = SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code="ARM01"
    ).all()
    assert {(c.box_code, c.pieces_per_box) for c in arm01_caps} == {
        ("XLG", 10)
    }
    # ARM02 added.
    arm02_caps = SCTissueBoxCapacity.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code="ARM02"
    ).all()
    assert {(c.box_code, c.pieces_per_box) for c in arm02_caps} == {
        ("MED", 6), ("LRG", 10), ("XLG", 15)
    }


def test_tissue_codes_download_round_trips_customer_template(
    app: Flask,
) -> None:
    # Download produces the customer template's exact column shape
    # (Medium / Large / X-Large / Small Airtray / Airtray). Zeros fill
    # every cell that has no capacity row.
    db.session.add_all(
        [
            SCTissueCode(
                tissue_code="ARM01",
                description="Arm Whole",
                unit_weight_lb=12.0,
            ),
            SCTissueBoxCapacity(
                tissue_code="ARM01", box_code="LRG", pieces_per_box=7
            ),
            SCTissueBoxCapacity(
                tissue_code="ARM01", box_code="XLG", pieces_per_box=10
            ),
        ]
    )
    db.session.commit()
    user = _make_user(
        "dl-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_tissue_codes/download")
    assert response.status_code == 200
    body = response.get_data(as_text=True).splitlines()
    assert body[0] == (
        "Tissue Code,Description,Unit Weight (lb),"
        "Medium,Large,X-Large,Small Airtray,Airtray,Notes"
    )
    assert body[1].startswith("ARM01,Arm Whole,12")
    cells = body[1].split(",")
    # Medium / Large / X-Large / Small Airtray / Airtray
    assert cells[3:8] == ["0", "7", "10", "0", "0"]


def test_tissue_codes_upload_rejects_negative_pieces(app: Flask) -> None:
    user = _make_user(
        "neg-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    csv = _tissue_csv("ARM01,Arm Whole,12,0,-5,0,0,0,")
    response = client.post(
        "/sc/reference/sc_tissue_codes/upload",
        data={"file": (csv, "tissue.csv"), "action": "replace"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    # 400 from form validation error.
    assert response.status_code == 400
    assert SCTissueCode.query.count() == 0


# --- Per-row CRUD routes ----------------------------------------------------
#
# Mirrors /admin/accessorials (Add Row / Edit / Delete) but scoped to
# rate_set == "science_care". Drives the generic list/form templates and
# the tissue-codes special-case form (parent + capacities) the routes
# layer on top of the existing TableSpec metadata.


def _login_sc_admin(app: Flask, email: str) -> FlaskClient:
    user = _make_user(email, rate_set=RATE_SET_SCIENCE_CARE, is_sc_admin=True)
    client = app.test_client()
    _login(client, user.id)
    return client


def test_reference_list_blocked_for_plain_sc_user(app: Flask) -> None:
    user = _make_user(
        "plain-list@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference/sc_labs")
    assert response.status_code == 403


def test_reference_list_unknown_table_returns_404(app: Flask) -> None:
    client = _login_sc_admin(app, "list-404@example.com")
    response = client.get("/sc/reference/not_a_table")
    assert response.status_code == 404


def test_reference_list_renders_only_sc_rows(app: Flask) -> None:
    # Two labs at the same rate-set the SC admin owns and one for a
    # different tenant; only the SC rows must show up.
    client = _login_sc_admin(app, "list-renders@example.com")
    db.session.add_all(
        [
            SCLab(
                lab_code="SCCA",
                lab_name="Tucson",
                origin_zip="85705",
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCLab(
                lab_code="OTHER",
                lab_name="Other Tenant",
                origin_zip="99999",
                rate_set="other_tenant",
            ),
        ]
    )
    db.session.commit()
    response = client.get("/sc/reference/sc_labs")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "SCCA" in html
    assert "Tucson" in html
    assert "OTHER" not in html


def test_reference_new_lab_persists_with_sc_rate_set(app: Flask) -> None:
    client = _login_sc_admin(app, "new-lab@example.com")
    response = client.post(
        "/sc/reference/sc_labs/new",
        data={
            "lab_code": "SCCA",
            "lab_name": "Tucson",
            "origin_zip": "85705",
            "address": "",
            "contact_name": "",
            "contact_phone": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    row = SCLab.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, lab_code="SCCA"
    ).one()
    assert row.lab_name == "Tucson"
    assert row.origin_zip == "85705"
    assert row.is_active is True


def test_reference_new_rejects_duplicate_unique_key(app: Flask) -> None:
    client = _login_sc_admin(app, "dup@example.com")
    db.session.add(
        SCLab(
            lab_code="SCCA",
            lab_name="First",
            origin_zip="85705",
            rate_set=RATE_SET_SCIENCE_CARE,
        )
    )
    db.session.commit()
    response = client.post(
        "/sc/reference/sc_labs/new",
        data={
            "lab_code": "SCCA",
            "lab_name": "Dup",
            "origin_zip": "11111",
        },
    )
    assert response.status_code == 400
    assert "already exists" in response.get_data(as_text=True).lower()
    assert SCLab.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).count() == 1


def test_reference_new_rejects_missing_required_field(app: Flask) -> None:
    client = _login_sc_admin(app, "missing@example.com")
    response = client.post(
        "/sc/reference/sc_labs/new",
        data={"lab_code": "", "origin_zip": "85705"},
    )
    assert response.status_code == 400
    assert "enter a value" in response.get_data(as_text=True).lower()


def test_reference_edit_updates_row(app: Flask) -> None:
    client = _login_sc_admin(app, "edit@example.com")
    lab = SCLab(
        lab_code="SCCA",
        lab_name="Tucson",
        origin_zip="85705",
        is_active=True,
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(lab)
    db.session.commit()
    response = client.post(
        f"/sc/reference/sc_labs/{lab.id}/edit",
        data={
            "lab_code": "SCCA",
            "lab_name": "Renamed",
            "origin_zip": "85705",
            "address": "",
            "contact_name": "",
            "contact_phone": "",
            # is_active omitted → checkbox unchecked
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    refreshed = db.session.get(SCLab, lab.id)
    assert refreshed.lab_name == "Renamed"
    assert refreshed.is_active is False


def test_reference_edit_other_tenant_returns_404(app: Flask) -> None:
    client = _login_sc_admin(app, "other-edit@example.com")
    lab = SCLab(
        lab_code="OTHER",
        lab_name="Other",
        origin_zip="99999",
        rate_set="other_tenant",
    )
    db.session.add(lab)
    db.session.commit()
    response = client.get(f"/sc/reference/sc_labs/{lab.id}/edit")
    assert response.status_code == 404


def test_reference_delete_removes_row(app: Flask) -> None:
    client = _login_sc_admin(app, "del@example.com")
    lab = SCLab(
        lab_code="SCCA",
        lab_name="Tucson",
        origin_zip="85705",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(lab)
    db.session.commit()
    lab_id = lab.id
    response = client.post(
        f"/sc/reference/sc_labs/{lab_id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert db.session.get(SCLab, lab_id) is None


def test_reference_delete_other_tenant_returns_404(app: Flask) -> None:
    client = _login_sc_admin(app, "del-other@example.com")
    lab = SCLab(
        lab_code="OTHER",
        lab_name="Other",
        origin_zip="99999",
        rate_set="other_tenant",
    )
    db.session.add(lab)
    db.session.commit()
    response = client.post(f"/sc/reference/sc_labs/{lab.id}/delete")
    assert response.status_code == 404
    # Row untouched.
    assert db.session.get(SCLab, lab.id) is not None


def test_tissue_new_form_persists_parent_and_capacities(app: Flask) -> None:
    # Adding a tissue code with per-box quantities must write both the
    # SCTissueCode parent row and one SCTissueBoxCapacity per non-zero
    # quantity, and recompute the parent's default_box_type_code +
    # pieces_per_box hint from the box with the largest capacity.
    client = _login_sc_admin(app, "tissue-new@example.com")
    db.session.add_all(
        [
            SCBoxType(
                code="MED",
                label="Medium",
                length_in=10,
                width_in=10,
                height_in=10,
                tare_weight_lb=2,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCBoxType(
                code="LRG",
                label="Large",
                length_in=15,
                width_in=15,
                height_in=15,
                tare_weight_lb=3,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()
    response = client.post(
        "/sc/reference/sc_tissue_codes/new",
        data={
            "tissue_code": "BONE",
            "description": "Femur",
            "unit_weight_lb": "0.5",
            "notes": "fragile",
            "cap_MED": "4",
            "cap_LRG": "8",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    tissue = SCTissueCode.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE, tissue_code="BONE"
    ).one()
    assert tissue.default_box_type_code == "LRG"
    assert tissue.pieces_per_box == 8
    caps = sorted(
        (c.box_code, c.pieces_per_box)
        for c in SCTissueBoxCapacity.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE, tissue_code="BONE"
        ).all()
    )
    assert caps == [("LRG", 8), ("MED", 4)]


def test_tissue_edit_replaces_capacities_and_default_box(app: Flask) -> None:
    # Editing a tissue must wipe + reinsert the per-box matrix so a
    # capacity cleared in the form disappears from the DB (not just
    # zeroed out).
    client = _login_sc_admin(app, "tissue-edit@example.com")
    db.session.add_all(
        [
            SCBoxType(
                code="MED",
                label="Medium",
                length_in=10,
                width_in=10,
                height_in=10,
                tare_weight_lb=2,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCBoxType(
                code="LRG",
                label="Large",
                length_in=15,
                width_in=15,
                height_in=15,
                tare_weight_lb=3,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueCode(
                tissue_code="BONE",
                description="Femur",
                unit_weight_lb=0.5,
                default_box_type_code="LRG",
                pieces_per_box=8,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueBoxCapacity(
                tissue_code="BONE",
                box_code="MED",
                pieces_per_box=4,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueBoxCapacity(
                tissue_code="BONE",
                box_code="LRG",
                pieces_per_box=8,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()
    tissue_id = SCTissueCode.query.one().id
    response = client.post(
        f"/sc/reference/sc_tissue_codes/{tissue_id}/edit",
        data={
            "tissue_code": "BONE",
            "description": "Updated",
            "unit_weight_lb": "0.6",
            "notes": "",
            "cap_MED": "6",
            "cap_LRG": "",  # cleared → row deleted
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    refreshed = db.session.get(SCTissueCode, tissue_id)
    assert refreshed.description == "Updated"
    assert refreshed.default_box_type_code == "MED"
    assert refreshed.pieces_per_box == 6
    caps = sorted(
        (c.box_code, c.pieces_per_box)
        for c in SCTissueBoxCapacity.query.filter_by(
            rate_set=RATE_SET_SCIENCE_CARE
        ).all()
    )
    assert caps == [("MED", 6)]


def test_tissue_edit_ignores_renamed_tissue_code(app: Flask) -> None:
    # The tissue_code is the join key for SCTissueBoxCapacity, so the
    # edit view pins it to the stored value even when a (presumably
    # tampered) POST submits a different string. Capacities must stay
    # attached to the original code.
    client = _login_sc_admin(app, "tissue-rename@example.com")
    db.session.add_all(
        [
            SCBoxType(
                code="MED",
                label="Medium",
                length_in=10,
                width_in=10,
                height_in=10,
                tare_weight_lb=2,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueCode(
                tissue_code="BONE",
                description="Femur",
                unit_weight_lb=0.5,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueBoxCapacity(
                tissue_code="BONE",
                box_code="MED",
                pieces_per_box=4,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()
    tissue_id = SCTissueCode.query.one().id
    response = client.post(
        f"/sc/reference/sc_tissue_codes/{tissue_id}/edit",
        data={
            "tissue_code": "RENAMED",
            "description": "Femur",
            "unit_weight_lb": "0.5",
            "notes": "",
            "cap_MED": "4",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    refreshed = db.session.get(SCTissueCode, tissue_id)
    assert refreshed.tissue_code == "BONE"
    cap = SCTissueBoxCapacity.query.one()
    assert cap.tissue_code == "BONE"


def test_box_type_delete_cascades_to_capacities(app: Flask) -> None:
    # Box-type rows are referenced by SCTissueBoxCapacity via a string
    # box_code column (no FK / cascade), so deleting the parent has to
    # wipe matching capacity rows manually and refresh each affected
    # tissue's default-box hint.
    client = _login_sc_admin(app, "box-del@example.com")
    db.session.add_all(
        [
            SCBoxType(
                code="MED",
                label="Medium",
                length_in=10,
                width_in=10,
                height_in=10,
                tare_weight_lb=2,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCBoxType(
                code="LRG",
                label="Large",
                length_in=15,
                width_in=15,
                height_in=15,
                tare_weight_lb=3,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueCode(
                tissue_code="BONE",
                description="Femur",
                unit_weight_lb=0.5,
                default_box_type_code="LRG",
                pieces_per_box=8,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueBoxCapacity(
                tissue_code="BONE",
                box_code="MED",
                pieces_per_box=4,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueBoxCapacity(
                tissue_code="BONE",
                box_code="LRG",
                pieces_per_box=8,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()
    lrg_id = SCBoxType.query.filter_by(code="LRG").one().id
    response = client.post(
        f"/sc/reference/sc_box_types/{lrg_id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 302
    # LRG box gone, and capacities referencing it are gone too.
    assert SCBoxType.query.filter_by(code="LRG").count() == 0
    caps = sorted(
        (c.box_code, c.pieces_per_box)
        for c in SCTissueBoxCapacity.query.all()
    )
    assert caps == [("MED", 4)]
    # Default-box hint on the parent tissue is refreshed to MED so it
    # doesn't dangle on the now-deleted LRG.
    refreshed = SCTissueCode.query.one()
    assert refreshed.default_box_type_code == "MED"
    assert refreshed.pieces_per_box == 4


def test_tissue_delete_cascades_to_capacities(app: Flask) -> None:
    client = _login_sc_admin(app, "tissue-del@example.com")
    db.session.add_all(
        [
            SCTissueCode(
                tissue_code="BONE",
                description="Femur",
                unit_weight_lb=0.5,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueBoxCapacity(
                tissue_code="BONE",
                box_code="MED",
                pieces_per_box=4,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()
    tissue_id = SCTissueCode.query.one().id
    response = client.post(
        f"/sc/reference/sc_tissue_codes/{tissue_id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert SCTissueCode.query.count() == 0
    assert SCTissueBoxCapacity.query.count() == 0


def test_reference_index_shows_view_edit_link(app: Flask) -> None:
    client = _login_sc_admin(app, "index-link@example.com")
    response = client.get("/sc/reference")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "View / Edit Rows" in html
    assert "/sc/reference/sc_labs" in html
