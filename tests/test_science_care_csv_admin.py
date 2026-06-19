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
        full_name=email,
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
    assert [r.code for r in rows] == ["LRG", "MED"]
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
