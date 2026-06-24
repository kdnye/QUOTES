"""Route auth + render tests for the Science Care blueprint scaffold.

Covers the PR-B surface: the quote form and HTMX partials, the reference
index landing page, and the htmx tag in base.html.

The orchestration POST and the CSV upload/download endpoints land in
follow-up PRs and are not exercised here.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.models import (
    RATE_SET_SCIENCE_CARE,
    SCBoxType,
    SCLab,
    SCTissueBoxCapacity,
    SCTissueCode,
    User,
    db,
)


class TestSCRoutesConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestSCRoutesConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestSCRoutesConfig)
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


def test_sc_quote_renders_for_sc_user(app: Flask) -> None:
    user = _make_user("sc@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # All seven legs render.
    for n in range(1, 8):
        assert f"SHIPMENT {n}" in html
    # HTMX script tag from base.html is present. Use the pinned URL so
    # CodeQL doesn't flag a loose "htmx.org" substring as incomplete URL
    # sanitization (it is a regression assertion, not a security check).
    assert "unpkg.com/htmx.org@1.9.12" in html


def test_sc_quote_blocks_non_sc_user(app: Flask) -> None:
    user = _make_user("non-sc@example.com", rate_set="default")
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 403


def test_sc_reference_blocks_plain_sc_user(app: Flask) -> None:
    user = _make_user("plain@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference")
    assert response.status_code == 403


def test_sc_reference_allows_sc_admin(app: Flask) -> None:
    user = _make_user(
        "sc-admin@example.com",
        rate_set=RATE_SET_SCIENCE_CARE,
        is_sc_admin=True,
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/reference")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # All six reference tables listed.
    for key in (
        "Labs",
        "Tissue codes",
        "Box types",
        "Consumables",
        "Established lanes",
        "Accessorial map",
    ):
        assert key in html


def test_sc_dest_zip_notes_renders_banner(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ZIP-based shipment notes surface in the per-leg banner.

    Stubs :func:`app.services.quote.get_zip_notes` so the test does
    not depend on the ``zip_zones.notes`` column being present (a
    pre-existing schema drift between the SQLAlchemy model and the
    migration history that the rest of the SC test suite works around
    via :data:`tests/conftest.py::_KNOWN_FAILURE_NODEIDS`).
    """

    from app.science_care import routes as sc_routes

    def _fake_get_zip_notes(zip_code, rate_set, session=None):
        if zip_code == "30301":
            return (
                "Destination Airport Cargo Warnings: Airtray "
                "Restrictions on Weekends"
            )
        return None

    monkeypatch.setattr(sc_routes, "get_zip_notes", _fake_get_zip_notes)

    user = _make_user("notes@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/dest-zip-notes?leg=2&dest_zip_2=30301"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="dest-zip-notes-2"' in html
    assert "Airtray Restrictions on Weekends" in html
    assert "ZIP 30301" in html


def test_sc_dest_zip_notes_empty_when_no_match(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-match still returns an empty target div so HTMX can swap again."""

    from app.science_care import routes as sc_routes

    monkeypatch.setattr(
        sc_routes, "get_zip_notes", lambda *a, **k: None
    )

    user = _make_user(
        "notes-empty@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/dest-zip-notes?leg=5&dest_zip_5=99999"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Stable swap target survives even when no notes exist - HTMX
    # re-targets this div on the next ZIP change.
    assert 'id="dest-zip-notes-5"' in html
    assert "fsi-notice--warning" not in html


def test_sc_quote_form_renders_accessorial_costs(app: Flask) -> None:
    """Accessorial checkboxes show the dollar (or %) cost beside the label."""

    from app.models import Accessorial, SCAccessorialMap

    user = _make_user(
        "acc-cost@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    db.session.add_all(
        [
            Accessorial(name="Liftgate", amount=85.0, is_percentage=False),
            SCAccessorialMap(
                form_field="J8",
                display_label="Liftgate Required",
                accessorial_name="Liftgate",
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    html = client.get("/sc/quote").get_data(as_text=True)
    assert "Liftgate Required" in html
    assert "$85.00" in html


def test_sc_quote_form_renders_static_guidance_notes(app: Flask) -> None:
    """Static workbook-derived guidance notes render on the SC quote page."""

    user = _make_user(
        "guidance@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    html = client.get("/sc/quote").get_data(as_text=True)
    # Accessorial banner reminding the operator the fees apply both
    # ways. The ampersand is HTML-escaped (``&amp;``) by Jinja's
    # autoescape so assert on the entity, not the literal ``&``.
    assert "These ancillary fees apply for both RETURNS &amp; PICK-UPS" in html
    # Box-sizing guidance between the tissue rows and the Boxes section.
    assert "Always quote the larger size" in html
    assert "ask the lab how they would" in html


def test_sc_lab_lookup_returns_origin(app: Flask) -> None:
    user = _make_user("lab@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add(
        SCLab(
            lab_code="SCCA",
            lab_name="Tucson",
            origin_zip="85705",
            is_active=True,
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/lab-lookup?leg=1&code=SCCA")
    assert response.status_code == 200
    assert "85705" in response.get_data(as_text=True)


def test_sc_lab_lookup_unknown_code(app: Flask) -> None:
    user = _make_user("lab2@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/lab-lookup?leg=1&code=NOPE")
    assert response.status_code == 200
    assert "No active lab" in response.get_data(as_text=True)


def test_sc_tissue_row_partial_blank(app: Flask) -> None:
    user = _make_user(
        "tissue@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/tissue-row?leg=3&i=4")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="tissue_code_3_4"' in html


def test_sc_tissue_lookup_prefills_known_code(app: Flask) -> None:
    user = _make_user(
        "tissue2@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    db.session.add_all(
        [
            SCBoxType(
                code="XL",
                label="X-Large",
                length_in=52,
                width_in=20,
                height_in=15,
                tare_weight_lb=14.0,
            ),
            SCTissueCode(
                tissue_code="PELV03",
                description="Pelvis to Toe",
                unit_weight_lb=79.0,
                default_box_type_code="XL",
                pieces_per_box=1,
            ),
        ]
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pelvis to Toe" in html
    assert "79.00" in html
    # XL is the legacy default_box_type_code; it lands in the new per-row
    # box dropdown as the only option (the SCTissueBoxCapacity table is
    # empty for this tissue, so the route falls back to the legacy field).
    assert "XL" in html


def test_sc_tissue_lookup_unknown_code_preserves_input(app: Flask) -> None:
    user = _make_user(
        "tissue3@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=NOPE"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Bootstrap invalid styling + the typed code preserved.
    assert "is-invalid" in html
    assert 'value="NOPE"' in html
    assert "Unknown tissue code" in html


def test_sc_tissue_lookup_accepts_dynamic_param_name(app: Flask) -> None:
    # HTMX sends the input's `name` (tissue_code_<leg>_<i>) as the query
    # parameter, not `code`. Verify the fallback resolves it.
    user = _make_user(
        "tissue4@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    db.session.add(
        SCTissueCode(
            tissue_code="PELV03",
            description="Pelvis to Toe",
            unit_weight_lb=79.0,
            default_box_type_code="XL",
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=2&i=3&tissue_code_2_3=PELV03"
    )
    assert response.status_code == 200
    assert "Pelvis to Toe" in response.get_data(as_text=True)


def test_sc_tissue_lookup_emits_box_count_oob(app: Flask) -> None:
    # Tissue-code change must piggy-back an out-of-band box-count swap so
    # the Boxes section catches up in the same round-trip - no need for
    # the user to also nudge a qty input.
    user = _make_user(
        "tissue-oob@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _med, xlg, _ = _seed_box_count_endpoint_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03"
        "&tissue_code_1_1=PELV03&qty_1_1=2"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Tissue row partial: input populated with the resolved code.
    assert 'name="tissue_code_1_1"' in html
    assert 'value="PELV03"' in html
    # OOB box-counts wrapper carries the hx-swap-oob attribute.
    assert 'id="box-counts-1"' in html
    assert 'hx-swap-oob="outerHTML"' in html
    # XLG auto-fills to 2 (PELV03 default + qty=2, pieces_per_box=1).
    xlg_slice = html.split(f'name="box_count_1_{xlg.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value="2"' in xlg_slice


def test_sc_tissue_lookup_oob_preserves_typed_box_overrides(
    app: Flask,
) -> None:
    # The OOB recompute must respect any non-blank typed box-count
    # override - same "prefill empty inputs only" rule the qty trigger
    # uses.
    user = _make_user(
        "tissue-oob-override@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    med, xlg, _ = _seed_box_count_endpoint_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03"
        "&tissue_code_1_1=PELV03&qty_1_1=2"
        f"&box_count_1_{med.id}=4"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    med_slice = html.split(f'name="box_count_1_{med.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value="4"' in med_slice
    xlg_slice = html.split(f'name="box_count_1_{xlg.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value="2"' in xlg_slice


def test_sc_tissue_lookup_oob_preserves_explicit_zero(app: Flask) -> None:
    # Regression mirror of the qty-trigger test: an explicit "0" must
    # survive the OOB recompute too.
    user = _make_user(
        "tissue-oob-zero@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _med, xlg, _ = _seed_box_count_endpoint_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03"
        "&tissue_code_1_1=PELV03&qty_1_1=2"
        f"&box_count_1_{xlg.id}=0"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    xlg_slice = html.split(f'name="box_count_1_{xlg.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value="0"' in xlg_slice


def test_sc_lab_lookup_accepts_dynamic_param_name(app: Flask) -> None:
    user = _make_user("lab3@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add(
        SCLab(
            lab_code="SCAZ",
            lab_name="Phoenix",
            origin_zip="85040",
            is_active=True,
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/lab-lookup?leg=5&lab_code_5=SCAZ"
    )
    assert response.status_code == 200
    assert "85040" in response.get_data(as_text=True)


def test_sc_lookup_endpoints_survive_garbage_query_params(app: Flask) -> None:
    user = _make_user(
        "garbage@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    for path in (
        "/sc/quote/lab-lookup?leg=abc",
        "/sc/quote/tissue-row?leg=NaN&i=oops",
        "/sc/quote/tissue-lookup?leg=&i=&code=",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_sc_quote_calculate_renders_results(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user("post-orch@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add_all(
        [
            SCLab(
                lab_code="SCCA",
                origin_zip="85705",
                is_active=True,
            ),
            __import__("app.models", fromlist=["SCBoxType"]).SCBoxType(
                code="MED",
                length_in=20,
                width_in=15,
                height_in=18,
                tare_weight_lb=4,
            ),
            SCTissueCode(
                tissue_code="MED01",
                description="Medium part",
                unit_weight_lb=10.0,
                default_box_type_code="MED",
                pieces_per_box=2,
            ),
        ]
    )
    db.session.commit()

    # Stub create_quote so the route doesn't depend on the rate tables.
    from app.services import science_care_quote as svc

    def fake_create_quote(**kwargs):
        from app.models import Quote

        q = Quote(
            user_id=kwargs.get("user_id"),
            user_email=kwargs.get("user_email"),
            quote_type=kwargs["quote_type"],
            origin=kwargs["origin"],
            destination=kwargs["destination"],
            weight=kwargs["weight"],
            pieces=kwargs.get("pieces", 1),
            zone="X",
            total=100.0 if kwargs["quote_type"] == "Air" else 80.0,
            quote_metadata="{}",
            rate_set=kwargs.get("rate_set"),
        )
        db.session.add(q)
        db.session.commit()
        db.session.refresh(q)
        return q, {"total": q.total, "details": {}}

    monkeypatch.setattr(svc, "create_quote", fake_create_quote)

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/calculate",
        data={
            "lab_code_1": "SCCA",
            "dest_zip_1": "98101",
            "routing_type_1": "Outbound",
            "temp_mode_1": "frozen",
            "tissue_code_1_1": "MED01",
            "qty_1_1": "2",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Multi-leg quote summary" in html
    assert "Hot Shot" in html
    assert "Total S&amp;H" in html or "Total S&H" in html
    # Weight breakdown columns must surface in the results card.
    assert "Tissue (lb)" in html
    assert "Consumables (lb)" in html
    assert "Box tare (lb)" in html
    assert "Total weight (lb)" in html
    # And the per-leg figures land in the row. MED01 qty 2 @ 10 lb each
    # = 20 lb tissue; 1 MED box (4 lb tare); no SCConsumable rows seeded
    # so consumables fall through to 0. Total = 24 lb.
    assert "20.0" in html  # tissue weight
    assert "24.0" in html  # total weight


def test_sc_quote_form_renders_consumable_inputs(app: Flask) -> None:
    # The new per-leg Consumables section must surface one numeric
    # input per SCConsumable row, named cons_qty_<leg>_<id>.
    from app.models import SCConsumable

    user = _make_user("cons-form@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    cons = SCConsumable(
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    db.session.add(cons)
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Section header is present.
    assert ">Consumables<" in html
    # Each leg renders a Qty input keyed on the consumable's id.
    for leg in range(1, 8):
        assert f'name="cons_qty_{leg}_{cons.id}"' in html


def test_sc_quote_form_renders_box_count_inputs(app: Flask) -> None:
    # The new per-leg Boxes section surfaces one Count input per
    # SCBoxType, named box_count_<leg>_<id>.
    from app.models import SCBoxType

    user = _make_user("box-form@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    box = SCBoxType(
        code="MED",
        label="Medium",
        length_in=20,
        width_in=15,
        height_in=18,
        tare_weight_lb=4.0,
    )
    db.session.add(box)
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert ">Boxes<" in html
    for leg in range(1, 8):
        assert f'name="box_count_{leg}_{box.id}"' in html
        # The wrapping div is the HTMX swap target - if this id moves,
        # the live-recompute trigger has nowhere to land.
        assert f'id="box-counts-{leg}"' in html


def _seed_box_count_endpoint_fixtures():
    """Seed the SC reference data the box-count partial endpoint reads."""

    from app.models import SCBoxType, SCTissueCode

    med = SCBoxType(
        code="MED",
        label="Medium",
        length_in=20,
        width_in=15,
        height_in=18,
        tare_weight_lb=4.0,
    )
    xlg = SCBoxType(
        code="XLG",
        label="X-Large",
        length_in=52,
        width_in=20,
        height_in=15,
        tare_weight_lb=14.0,
    )
    pelv = SCTissueCode(
        tissue_code="PELV03",
        description="Pelvis to Toe",
        unit_weight_lb=79.0,
        default_box_type_code="XLG",
        pieces_per_box=1,
    )
    db.session.add_all([med, xlg, pelv])
    db.session.commit()
    return med, xlg, pelv


def test_sc_box_counts_partial_returns_auto_values(app: Flask) -> None:
    # Submit tissue rows; the endpoint returns the box-count grid with
    # the XLG count auto-filled and the others left blank.
    user = _make_user("auto-box@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    med, xlg, _ = _seed_box_count_endpoint_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={"tissue_code_1_1": "PELV03", "qty_1_1": "2"},
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # OOB swap wrapper is rendered.
    assert 'id="box-counts-1"' in html
    # XLG resolves to value="2" (PELV03 default + qty=2 with pieces_per_box=1).
    assert (
        f'name="box_count_1_{xlg.id}"' in html
        and 'value="2"' in html.split(f'name="box_count_1_{xlg.id}"', 1)[1]
        .split("</div>", 1)[0]
    )
    # MED stays empty since no tissue maps to it.
    med_slice = html.split(f'name="box_count_1_{med.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value=""' in med_slice


def test_sc_box_counts_partial_preserves_typed_overrides(app: Flask) -> None:
    # When the user has typed a MED count, the recompute keeps it (the
    # XLG auto still flows in for the still-empty input).
    user = _make_user("override-box@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    med, xlg, _ = _seed_box_count_endpoint_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "2",
            f"box_count_1_{med.id}": "4",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # MED kept the user's typed override.
    med_slice = html.split(f'name="box_count_1_{med.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value="4"' in med_slice
    # XLG still auto-fills to 2.
    xlg_slice = html.split(f'name="box_count_1_{xlg.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'value="2"' in xlg_slice


def test_sc_box_counts_partial_preserves_explicit_zero(app: Flask) -> None:
    # Regression: an explicit "0" is a deliberate "no boxes of this
    # type" override, NOT an empty input. It must survive the recompute
    # even when the auto allocation would put boxes here.
    user = _make_user(
        "zero-override@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _med, xlg, _ = _seed_box_count_endpoint_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "2",
            # Auto would put 2 XLG boxes here. User explicitly set 0.
            f"box_count_1_{xlg.id}": "0",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    xlg_slice = html.split(f'name="box_count_1_{xlg.id}"', 1)[1].split(
        "</div>", 1
    )[0]
    # The "0" is preserved; the auto did not creep back in.
    assert 'value="0"' in xlg_slice


def _seed_box_counts_consumable_fixtures():
    """Add the SCConsumable rows the partial needs to compute defaults."""

    from app.models import SCConsumable

    dry_ice = SCConsumable(
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    gel_pack = SCConsumable(
        consumable_type="gel_pack",
        temp_mode="rtu",
        scope="domestic",
        weight_lb_per_box=20.0,
    )
    db.session.add_all([dry_ice, gel_pack])
    db.session.commit()
    return dry_ice, gel_pack


def test_sc_box_counts_partial_surfaces_consumable_auto_default(
    app: Flask,
) -> None:
    # Regression: the live recompute used to add the temp_mode default
    # consumable (1 per box) to the subtotal without re-rendering its
    # Qty input, so the page showed a non-zero Consumables subtotal
    # while every input still read "0".
    user = _make_user(
        "cons-auto@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_box_count_endpoint_fixtures()
    dry_ice, gel_pack = _seed_box_counts_consumable_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "2",
            "temp_mode_1": "frozen",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # OOB wrapper for the consumables grid rides along on the response.
    assert 'id="cons-qty-inputs-1"' in html
    # The Frozen-domestic dry ice input now reflects the 2-box default.
    dry_ice_slice = html.split(
        f'name="cons_qty_1_{dry_ice.id}"', 1
    )[1].split("</div>", 1)[0]
    assert 'value="2"' in dry_ice_slice
    # Non-matching consumable (gel pack) stays blank so the placeholder
    # "0" shows - it would otherwise add noise to every row.
    gel_pack_slice = html.split(
        f'name="cons_qty_1_{gel_pack.id}"', 1
    )[1].split("</div>", 1)[0]
    assert 'value=""' in gel_pack_slice


def test_sc_box_counts_partial_preserves_consumable_zero(app: Flask) -> None:
    # Typing "0" into the temp_mode default consumable means "suppress
    # the auto-applied default". The recompute must keep the 0 visible
    # AND zero out the Consumables subtotal pill.
    user = _make_user(
        "cons-zero@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_box_count_endpoint_fixtures()
    dry_ice, _ = _seed_box_counts_consumable_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "2",
            "temp_mode_1": "frozen",
            f"cons_qty_1_{dry_ice.id}": "0",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    dry_ice_slice = html.split(
        f'name="cons_qty_1_{dry_ice.id}"', 1
    )[1].split("</div>", 1)[0]
    assert 'value="0"' in dry_ice_slice
    # Section pill rides on the same response; with the default
    # suppressed the Consumables subtotal collapses to 0 lb.
    cons_pill = html.split('id="sc-consumable-subtotal-1"', 1)[1].split(
        "</div>", 1
    )[0]
    assert "0.0 lb" in cons_pill


def test_sc_box_counts_partial_skips_cons_oob_when_user_types_in_cons_input(
    app: Flask,
) -> None:
    # When the user is actively typing in a consumable input, HTMX sets
    # HX-Trigger to that input's id. OOB-replacing the same container
    # would steal focus mid-keystroke and make multi-digit overrides
    # unusable, so the partial must drop the consumables swap in that
    # case (subtotals + box-counts still ride the response).
    user = _make_user(
        "cons-focus@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_box_count_endpoint_fixtures()
    dry_ice, _ = _seed_box_counts_consumable_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "2",
            "temp_mode_1": "frozen",
            f"cons_qty_1_{dry_ice.id}": "1",
        },
        headers={"HX-Trigger": f"cons_qty_1_{dry_ice.id}"},
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The consumables grid wrapper is NOT in the response - HTMX would
    # otherwise blow away the input the user is actively typing in.
    assert 'id="cons-qty-inputs-1"' not in html
    # The subtotals card + per-section pills still ride the response.
    assert 'id="sc-weight-subtotals-1"' in html
    assert 'id="sc-consumable-subtotal-1"' in html


def test_sc_box_counts_partial_keeps_cons_oob_for_other_triggers(
    app: Flask,
) -> None:
    # Sanity check: when the trigger is a tissue / qty / box input (or
    # the header is missing entirely), the OOB swap still rides the
    # response so the auto-default surfaces as boxes change.
    user = _make_user(
        "cons-keep@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_box_count_endpoint_fixtures()
    _seed_box_counts_consumable_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "2",
            "temp_mode_1": "frozen",
        },
        headers={"HX-Trigger": "qty_1_1"},
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="cons-qty-inputs-1"' in html


def test_sc_box_counts_partial_blocks_non_sc_user(app: Flask) -> None:
    user = _make_user("non-sc-box@example.com", rate_set="default")
    client = app.test_client()
    _login(client, user.id)
    response = client.post("/sc/quote/leg/1/box-counts", data={})
    assert response.status_code == 403


def test_base_template_loads_htmx() -> None:
    # Path-based check so the regression doesn't depend on rendering a
    # full request - the htmx script tag is mandatory for the SC page to
    # function and must not be accidentally removed.
    with open("templates/base.html", "r", encoding="utf-8") as fp:
        contents = fp.read()
    assert "htmx.org@1.9.12" in contents


def test_sc_tissue_lookup_dropdown_lists_capacity_boxes(app: Flask) -> None:
    # When SCTissueBoxCapacity rows exist for a tissue, the row's box
    # dropdown lists every box-size with non-zero capacity AND marks the
    # recommended one (smallest box count for the qty).
    user = _make_user("dd@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add_all(
        [
            SCBoxType(
                code="LRG",
                label="Large",
                length_in=32,
                width_in=18,
                height_in=20,
                tare_weight_lb=8.0,
            ),
            SCBoxType(
                code="XLG",
                label="X-Large",
                length_in=52,
                width_in=20,
                height_in=15,
                tare_weight_lb=14.0,
            ),
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
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=ARM01&qty_1_1=1"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Both allowed box codes appear as options; the smaller-volume LRG
    # is the recommendation for qty 1 (ties on box count → smaller box).
    assert "LRG" in html
    assert "XLG" in html
    assert "recommended" in html


def test_sc_tissue_lookup_dropdown_disabled_when_no_box_options(
    app: Flask,
) -> None:
    # A tissue with no capacity rows AND no legacy default renders the
    # dropdown disabled so the user can't accidentally submit a leg with
    # an unallocated tissue.
    user = _make_user("noopt@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    db.session.add(
        SCTissueCode(
            tissue_code="MOBILE KITS",
            description="Mobile Kit",
            unit_weight_lb=50.0,
        )
    )
    db.session.commit()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=MOBILE%20KITS"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "no box configured" in html
    # The dropdown is rendered as disabled.
    assert "disabled" in html


# --- Per-leg weight subtotals card -----------------------------------------


def _seed_subtotal_fixtures():
    """Tissue + capacity + box + consumable rows used by the subtotal tests."""

    from app.models import SCBoxType, SCConsumable, SCTissueBoxCapacity

    xlg = SCBoxType(
        code="XLG",
        label="X-Large",
        length_in=52,
        width_in=20,
        height_in=15,
        tare_weight_lb=14.0,
    )
    tissue = SCTissueCode(
        tissue_code="PELV03",
        description="Pelvis",
        unit_weight_lb=79.0,
    )
    cap = SCTissueBoxCapacity(
        tissue_code="PELV03", box_code="XLG", pieces_per_box=1
    )
    cons = SCConsumable(
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    db.session.add_all([xlg, tissue, cap, cons])
    db.session.commit()
    return xlg, cons


def test_sc_box_counts_partial_emits_weight_subtotals(app: Flask) -> None:
    # The qty / box-count endpoint must also emit the per-leg weight
    # subtotals card as an OOB swap so the form's "Shipment weight"
    # block stays in sync with whatever the user just changed.
    user = _make_user(
        "subtotal@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "1",
            "temp_mode_1": "frozen",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # OOB swap wrapper for the subtotals card.
    assert 'id="sc-weight-subtotals-1"' in html
    assert "Shipment weight" in html
    # 79 lb tissue + 14 lb XLG tare + 25 lb dry ice auto-default
    # (frozen leg, 1 box -> 1 domestic dry ice) = 118 lb total.
    assert "79.0" in html
    assert "14.0" in html
    assert "25.0" in html
    assert "118.0" in html


def test_tissue_lookup_default_qty_lands_in_oob_subtotals(app: Flask) -> None:
    # Regression: when the user types a tissue code without a qty, the
    # tissue-row partial defaults qty to 1 visually. The OOB subtotals
    # MUST see qty=1 too - otherwise the Shipment-weight card + the
    # three per-section pills stay at 0 lb until the user touches the
    # qty input, even though the row's Total lbs cell already shows
    # the line weight.
    user = _make_user(
        "default-qty@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    # No qty_1_1 in the query string - mimics the very first lookup
    # after the user types a code. Frozen leg + 1 XLG box also triggers
    # the dry-ice auto-default, so the recap TOTAL = tissue + box-tare
    # + 1 box × 25 lb dry ice.
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03&temp_mode_1=frozen"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="sc-weight-subtotals-1"' in html
    # Tissue 79 lb (qty 1 × 79) + XLG tare 14 lb + dry ice 25 lb
    # = 118 lb total.
    assert "118.0" in html
    # And the Tissue subsection pill carries 79.0 lb (the leg's tissue
    # subtotal, derived from qty=1 default - the regression bait).
    assert 'id="sc-tissue-subtotal-1"' in html
    assert "79.0 lb" in html


def test_sc_tissue_lookup_emits_subtotals_oob(app: Flask) -> None:
    # Tissue-code change must also refresh the subtotals card in the
    # same round-trip (alongside the OOB box-counts swap).
    user = _make_user(
        "sub-tissue@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03&qty_1_1=1&temp_mode_1=frozen"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="sc-weight-subtotals-1"' in html
    assert "93.0" in html  # total weight (tissue 79 + tare 14, no consumables)


def test_sc_tissue_row_renders_total_lbs_and_kg_columns(app: Flask) -> None:
    # The tissue row shows Total lbs (qty × avg) and Total kg
    # (rounded to whole kg). PELV03 qty 2 → 158 lb / 72 kg.
    user = _make_user("totals@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03&qty_1_1=2"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The total lbs input must carry qty × unit_weight (2 × 79 = 158 lb).
    assert 'name="tissue_total_lbs_1_1"' in html
    assert 'value="158.0"' in html
    # And the kg cell rounds 158 × 0.4536 = 71.67 → 72 kg.
    assert 'name="tissue_total_kg_1_1"' in html
    assert 'value="72"' in html


def test_quote_form_enables_htmx_template_fragments(app: Flask) -> None:
    # Regression: without useTemplateFragments=true, HTMX wraps tissue
    # row responses in a synthetic <table><tbody> for table-context
    # parsing. The OOB <div>s alongside the <tr> in the same response
    # are then foster-parented out of the table, and the main <tr>
    # swap silently stops applying - users see Description / Avg lbs
    # / Total lbs / Total kg / Box dropdown stay empty even though the
    # server returned the right data. The <meta name="htmx-config">
    # tag swaps the parse context to a <template> element where both
    # <tr> and <div> are valid siblings.
    user = _make_user(
        "tplfrag@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'name="htmx-config"' in body
    assert '"useTemplateFragments":true' in body


def test_consumable_inputs_carry_inline_hx_attrs_per_leg(app: Flask) -> None:
    # Regression: the consumable Qty inputs must carry per-leg HTMX
    # attributes inlined by Jinja so each input posts to its own leg's
    # /sc/quote/leg/<leg>/box-counts endpoint and includes only that
    # leg's form fields. An earlier implementation wired these from JS
    # using a regex on the input's name; that regex used /_(\d+)$/
    # which captured the trailing consumable_id from
    # cons_qty_<leg>_<id> and routed every request to the wrong leg.
    # Inlining the attributes per render avoids the regex entirely.
    from app.models import SCConsumable

    user = _make_user(
        "cons-hx@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    cons = SCConsumable(
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    db.session.add(cons)
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for leg in range(1, 8):
        # Each input carries its own leg-scoped hx-post / hx-include /
        # hx-target. Co-locating the input name and these attrs in the
        # same rendered fragment means the leg number can never drift.
        assert (
            f'hx-post="/sc/quote/leg/{leg}/box-counts"' in html
        ), f"leg {leg} missing hx-post"
        assert (
            f'hx-include="#sc-leg-{leg}"' in html
        ), f"leg {leg} missing hx-include"
        assert (
            f'hx-target="#box-counts-{leg}"' in html
        ), f"leg {leg} missing hx-target"
    # The JS wiring block is gone - guarantee no future contributor
    # re-introduces the brittle regex it relied on.
    assert "match(/_(\\d+)$/)" not in html


def test_box_counts_endpoint_emits_per_section_subtotals(app: Flask) -> None:
    # Three per-section subtotal pills (Consumables / Tissue / Boxes)
    # must ride the same /box-counts response as the recap card so the
    # numbers in each fieldset update without a separate round-trip.
    user = _make_user(
        "section-sub@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "1",
            "temp_mode_1": "frozen",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Each section's subtotal wrapper carries an OOB swap target.
    assert 'id="sc-consumable-subtotal-1"' in html
    assert 'id="sc-tissue-subtotal-1"' in html
    assert 'id="sc-box-subtotal-1"' in html
    # Subtotal pills carry the recap card's three numbers.
    # tissue 79 lb, no consumables entered (0 lb), XLG tare 14 lb.
    assert "79.0 lb" in html
    assert "0.0 lb" in html
    assert "14.0 lb" in html


def test_tissue_lookup_response_starts_with_tr_element(app: Flask) -> None:
    # Regression for a production bug: when this partial's response
    # carries leading whitespace from Jinja {% set %} blocks, the
    # browser's table-context parser drops the new <tr> and the HTMX
    # outerHTML swap silently no-ops. The server returns the right
    # data, but the row visually never updates. The fix is to use
    # whitespace-stripping {%- ... -%} on every leading block so the
    # response opens with the <tr> tag immediately.
    user = _make_user(
        "trswap@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        "/sc/quote/tissue-lookup?leg=1&i=1&code=PELV03&qty_1_1=1"
    )
    body = response.get_data(as_text=True)
    # No leading whitespace at all - the response must open directly
    # with the <tr> element so HTMX's table-fragment parser can find
    # it inside the synthesized <tbody> wrapper.
    assert body.lstrip("\n").startswith(
        body
    ), "tissue-lookup response must not start with leading newlines"
    assert body.startswith(
        "<tr"
    ), f"expected response to start with '<tr', got: {body[:40]!r}"


def test_box_count_inputs_carry_hx_post_for_live_subtotals(app: Flask) -> None:
    # Regression: typing a box-count override must trigger a recompute
    # of the per-leg Shipment-weight card. The override input needs its
    # own hx-post (the qty/consumable JS wiring on the parent page
    # can't reach OOB-swapped inputs).
    user = _make_user("box-input-hx@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    _seed_subtotal_fixtures()
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/leg/1/box-counts",
        data={
            "tissue_code_1_1": "PELV03",
            "qty_1_1": "1",
            "temp_mode_1": "frozen",
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The box-count input must declare an hx-post wired to the leg's
    # box-counts endpoint - without it, no recompute fires when the
    # user manually overrides a box count.
    assert 'name="box_count_1_' in html
    assert 'hx-post="/sc/quote/leg/1/box-counts"' in html
    # And it must use the input+debounce trigger so each keystroke
    # retunes the subtotals (matches the qty input behaviour).
    assert 'hx-trigger="input delay:300ms"' in html


# --- multi_reference / booking-email / lookup -------------------------------


def _seed_sc_session(
    user_id: int,
    *,
    multi_reference: str | None = "SCMQ0001",
    grand_total: float = 250.0,
) -> "SCQuoteSession":
    """Insert a minimal SCQuoteSession + one leg with linked Quote rows."""

    from app.models import (
        Quote,
        SCQuoteSession,
        SCQuoteSessionLeg,
    )

    session = SCQuoteSession(
        user_id=user_id,
        grand_total=grand_total,
        payload_json="{}",
        multi_reference=multi_reference,
    )
    db.session.add(session)
    db.session.flush()
    air = Quote(
        user_id=user_id,
        user_email="sc-buyer@example.com",
        quote_type="Air",
        origin="85705",
        destination="98101",
        weight=93.0,
        pieces=1,
        zone="X",
        total=300.0,
        quote_metadata="{}",
        rate_set=RATE_SET_SCIENCE_CARE,
        client_reference=(
            f"{multi_reference}-L1-AIR" if multi_reference else None
        ),
    )
    hot = Quote(
        user_id=user_id,
        user_email="sc-buyer@example.com",
        quote_type="Hotshot",
        origin="85705",
        destination="98101",
        weight=93.0,
        pieces=1,
        zone="X",
        total=250.0,
        quote_metadata="{}",
        rate_set=RATE_SET_SCIENCE_CARE,
        client_reference=(
            f"{multi_reference}-L1-HOT" if multi_reference else None
        ),
    )
    db.session.add_all([air, hot])
    db.session.flush()
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=1,
            air_quote_id=air.id,
            hotshot_quote_id=hot.id,
            winner_mode="Hotshot",
            winner_total=250.0,
        )
    )
    db.session.commit()
    return session


def test_sc_quote_form_renders_multi_reference_input(app: Flask) -> None:
    user = _make_user("sc-ref-form@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    html = client.get("/sc/quote").get_data(as_text=True)
    # New field surfaces with the auto-assign hint.
    assert 'name="multi_reference"' in html
    assert "Auto-assign" in html or "SCMQ" in html


def test_sc_email_ops_renders_with_no_booking_fee(app: Flask) -> None:
    user = _make_user(
        "sc-ops-email@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session = _seed_sc_session(user.id, multi_reference="SCMQ0042")
    client = app.test_client()
    _login(client, user.id)
    response = client.get(f"/sc/quote/{session.id}/email-ops")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The unified reference must appear in both the page header and the
    # generated email body (subject + body link).
    assert "SCMQ0042" in html
    # Explicit "no booking fee" copy must surface so ops + the user can
    # both confirm the divergence from the standard /quotes flow.
    assert "no booking fee" in html.lower() or "no booking fee" in html
    # The mailto: link must point at operations and not include a $15
    # admin fee suffix anywhere in the displayed total.
    assert "mailto:operations@freightservices.net" in html or "operations@freightservices.net" in html
    # Grand total surfaces as $250.00 - no $15 fee added.
    assert "$250.00" in html
    assert "$265.00" not in html  # i.e. NOT total + admin fee


def test_sc_email_ops_404s_for_unknown_session(app: Flask) -> None:
    user = _make_user(
        "sc-ops-404@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    assert (
        client.get("/sc/quote/999999/email-ops").status_code == 404
    )


def test_sc_email_ops_includes_per_leg_booking_details(app: Flask) -> None:
    """The booking email body must surface origin lab/city, dest
    city/state, accessorials, tissue items, boxes, and consumables
    per leg so ops can book without re-keying the form.
    """

    from app.models import (
        Quote,
        SCAccessorialMap,
        SCBoxType,
        SCConsumable,
        SCLab,
        SCQuoteSession,
        SCQuoteSessionLeg,
        SCTissueCode,
    )

    user = _make_user(
        "sc-ops-detail@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    # SC reference rows the booking-email page joins against.
    db.session.add_all(
        [
            SCLab(
                lab_code="TUC",
                lab_name="Tucson Recovery",
                origin_zip="85705",
                address="123 Lab Way, Tucson, AZ",
                is_active=True,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCAccessorialMap(
                form_field="J8",
                display_label="Liftgate Required",
                accessorial_name="Liftgate",
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCTissueCode(
                tissue_code="ARM01",
                description="Arm tissue",
                unit_weight_lb=2.5,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCBoxType(
                code="MED",
                label="Medium box",
                length_in=12.0,
                width_in=12.0,
                height_in=12.0,
                tare_weight_lb=2.0,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
            SCConsumable(
                consumable_type="dry_ice",
                temp_mode="frozen",
                scope="domestic",
                weight_lb_per_box=5.0,
                rate_set=RATE_SET_SCIENCE_CARE,
            ),
        ]
    )
    db.session.commit()

    cons = SCConsumable.query.filter_by(
        rate_set=RATE_SET_SCIENCE_CARE
    ).first()

    import json

    payload = {
        "multi_reference": "SCMQ0099",
        "lab_code_1": "TUC",
        "dest_zip_1": "98101",
        "temp_mode_1": "frozen",
        "tissue_code_1_1": "ARM01",
        "qty_1_1": "4",
        "acc_J8_1": "on",
    }
    session = SCQuoteSession(
        user_id=user.id,
        grand_total=250.0,
        payload_json=json.dumps(payload),
        multi_reference="SCMQ0099",
    )
    db.session.add(session)
    db.session.flush()
    air = Quote(
        user_id=user.id,
        user_email="sc-buyer@example.com",
        quote_type="Air",
        origin="85705",
        destination="98101",
        weight=15.0,
        pieces=1,
        zone="X",
        total=300.0,
        quote_metadata="{}",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    hot = Quote(
        user_id=user.id,
        user_email="sc-buyer@example.com",
        quote_type="Hotshot",
        origin="85705",
        destination="98101",
        weight=15.0,
        pieces=1,
        zone="X",
        total=250.0,
        quote_metadata="{}",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add_all([air, hot])
    db.session.flush()
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=1,
            air_quote_id=air.id,
            hotshot_quote_id=hot.id,
            winner_mode="Hotshot",
            winner_total=250.0,
            boxes_json=json.dumps({"MED": 2}),
            consumables_json=json.dumps({str(cons.id): 2}),
        )
    )
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    html = client.get(f"/sc/quote/{session.id}/email-ops").get_data(
        as_text=True
    )
    # Origin lab + city. Address parsing pulls "TUCSON, AZ" out of the
    # lab's address verbatim, so the city/state assertions are robust
    # to a missing Zipcode_Zones.csv in the test image.
    assert "TUC" in html
    assert "Tucson Recovery" in html
    assert "TUCSON" in html
    assert "AZ" in html
    # Destination city/state surfaces from the dest ZIP. ZIP lookup may
    # be a no-op when Zipcode_Zones.csv is missing in the test image, so
    # only assert the destination ZIP is present.
    assert "98101" in html
    # Accessorial display label, tissue item, box label, consumable
    # entry must all show.
    assert "Liftgate Required" in html
    assert "ARM01" in html
    assert "Arm tissue" in html
    assert "MED" in html
    assert "dry_ice" in html
    # Weight summary: subtotals per segment plus the shipment total.
    # Tissue: 4 x 2.5 = 10.00 lb. Boxes: 2 x 2.0 = 4.00 lb.
    # Consumables: 2 x 5.0 = 10.00 lb. Shipment total = 24.00 lb.
    assert "Tissue weight subtotal: 10.00 lb" in html
    assert "Boxes weight subtotal: 4.00 lb" in html
    assert "Consumable weight subtotal: 10.00 lb" in html
    assert "Shipment weight summary: 24.00 lb" in html


def test_sc_email_ops_omits_tare_breakdown_for_unknown_box(
    app: Flask,
) -> None:
    """If a leg's ``boxes_json`` references a code missing from the SC
    reference table, the booking email must still list the box (so ops
    can chase down the unknown code) but omit the ``lb/ea = lb`` weight
    breakdown - we have no tare to report. A valid 0-lb tare in the
    reference table, by contrast, should render normally.
    """

    from app.models import (
        Quote,
        SCBoxType,
        SCQuoteSession,
        SCQuoteSessionLeg,
    )

    user = _make_user(
        "sc-unknown-box@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    db.session.add(
        SCBoxType(
            code="ZERO",
            label="Zero-tare box",
            length_in=10.0,
            width_in=10.0,
            height_in=10.0,
            tare_weight_lb=0.0,
            rate_set=RATE_SET_SCIENCE_CARE,
        )
    )
    db.session.commit()

    import json

    session = SCQuoteSession(
        user_id=user.id,
        grand_total=100.0,
        payload_json=json.dumps({"multi_reference": "SCMQ0100"}),
        multi_reference="SCMQ0100",
    )
    db.session.add(session)
    db.session.flush()
    hot = Quote(
        user_id=user.id,
        user_email="sc-buyer@example.com",
        quote_type="Hotshot",
        origin="85705",
        destination="98101",
        weight=5.0,
        pieces=1,
        zone="X",
        total=100.0,
        quote_metadata="{}",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(hot)
    db.session.flush()
    db.session.add(
        SCQuoteSessionLeg(
            session_id=session.id,
            leg_index=1,
            hotshot_quote_id=hot.id,
            winner_mode="Hotshot",
            winner_total=100.0,
            boxes_json=json.dumps({"GHOST": 3, "ZERO": 2}),
        )
    )
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    html = client.get(f"/sc/quote/{session.id}/email-ops").get_data(
        as_text=True
    )
    # Unknown box GHOST is still listed (so ops can investigate) but
    # without the "( lb/ea = lb )" breakdown - we have no tare to print.
    assert "- GHOST x 3" in html
    assert "GHOST x 3 (" not in html
    # Valid 0-lb tare on ZERO must still render the breakdown - the
    # truthiness bug would have hidden this.
    assert "- ZERO (Zero-tare box) x 2 (0.00 lb/ea = 0.00 lb)" in html
    # Subtotal is 0 + 0 = 0 lb, not omitted.
    assert "Boxes weight subtotal: 0.00 lb" in html


def _seed_sc_session_with_legs(user_id: int):
    """Seed a minimal SC session + one leg so the composer-send route
    has something to render. Returns ``(session, leg)``.

    Kept inline rather than reusing ``_seed_sc_session`` because the
    Postmark send route also re-hydrates the leg (origin/destination
    weight) and we want the receipt's ``reference`` field assertion to
    have a stable value to compare against.
    """

    from app.models import (
        Quote,
        SCQuoteSession,
        SCQuoteSessionLeg,
    )
    import json

    session = SCQuoteSession(
        user_id=user_id,
        grand_total=250.0,
        payload_json=json.dumps({"multi_reference": "SCMQ0500"}),
        multi_reference="SCMQ0500",
    )
    db.session.add(session)
    db.session.flush()
    hot = Quote(
        user_id=user_id,
        user_email="sc-buyer@example.com",
        quote_type="Hotshot",
        origin="85705",
        destination="98101",
        weight=10.0,
        pieces=1,
        zone="X",
        total=250.0,
        quote_metadata="{}",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(hot)
    db.session.flush()
    leg = SCQuoteSessionLeg(
        session_id=session.id,
        leg_index=1,
        hotshot_quote_id=hot.id,
        winner_mode="Hotshot",
        winner_total=250.0,
    )
    db.session.add(leg)
    db.session.commit()
    return session, leg


_SAMPLE_INTAKE_FORM = {
    "pickup_date": "2026-07-01",
    "delivery_date": "2026-07-02",
    "shipper_name": "Acme Tissue Bank",
    "shipper_contact": "Jamie Shipper",
    "shipper_street": "123 Donor Way",
    "shipper_city": "Tucson",
    "shipper_state": "AZ",
    "shipper_zip": "85705",
    "shipper_phone": "555-111-2222",
    "shipper_reference": "ATB-9001",
    "shipper_notes": "Use rear dock. Closes at 4pm.",
    "consignee_name": "Recipient Lab",
    "consignee_contact": "Pat Consignee",
    "consignee_street": "789 Receiving Blvd",
    "consignee_city": "Seattle",
    "consignee_state": "WA",
    "consignee_zip": "98101",
    "consignee_phone": "555-333-4444",
    "consignee_reference": "RL-CASE-42",
    "consignee_notes": "Page on arrival.",
}


def test_sc_email_ops_intake_renders_form(app: Flask) -> None:
    """The intake GET surfaces all shipper / consignee / date fields
    so the SC user can fill them in. Empty session -> empty inputs.
    """

    user = _make_user(
        "sc-intake-get@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    response = client.get(
        f"/sc/quote/{session.id}/email-ops/intake"
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Every form field the parser knows about must have a matching
    # input on the page so the round-trip works.
    for prefix in ("shipper", "consignee"):
        for field in (
            "name",
            "street",
            "city",
            "state",
            "zip",
            "contact",
            "reference",
            "phone",
            "notes",
        ):
            assert f'name="{prefix}_{field}"' in html
    assert 'name="pickup_date"' in html
    assert 'name="delivery_date"' in html


def test_sc_email_ops_intake_post_persists_and_round_trips(
    app: Flask,
) -> None:
    """Submitting the intake form writes booking_intake_json,
    redirects to the composer, and pre-fills the form on a re-GET.
    """

    from app.models import SCQuoteSession

    user = _make_user(
        "sc-intake-post@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data=_SAMPLE_INTAKE_FORM,
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.location.endswith(
        f"/sc/quote/{session.id}/email-ops"
    )

    db.session.expire_all()
    persisted = SCQuoteSession.query.get(session.id)
    import json as _json

    intake = _json.loads(persisted.booking_intake_json)
    assert intake["pickup_date"] == "2026-07-01"
    assert intake["delivery_date"] == "2026-07-02"
    assert intake["shipper"]["name"] == "Acme Tissue Bank"
    assert intake["shipper"]["contact"] == "Jamie Shipper"
    assert intake["consignee"]["zip"] == "98101"
    assert intake["consignee"]["notes"] == "Page on arrival."

    # Re-GET pre-fills the values into the form.
    response = client.get(
        f"/sc/quote/{session.id}/email-ops/intake"
    )
    html = response.get_data(as_text=True)
    assert "Acme Tissue Bank" in html
    assert "RL-CASE-42" in html


def test_sc_email_ops_composer_shows_intake_card_when_populated(
    app: Flask,
) -> None:
    """The composer page surfaces the captured intake as a
    read-only card so the user can review before clicking send.
    """

    user = _make_user(
        "sc-intake-card@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data=_SAMPLE_INTAKE_FORM,
    )

    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    assert "Acme Tissue Bank" in html
    assert "Edit booking details" in html


def test_sc_email_ops_composer_shows_empty_state_without_intake(
    app: Flask,
) -> None:
    """When no intake has been captured, the composer prompts to
    capture it instead of rendering an empty card.
    """

    user = _make_user(
        "sc-intake-empty@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    assert "Add booking details" in html


def test_sc_email_ops_email_body_includes_intake_block(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The intake values must appear in BOTH the text body and the
    HTML body that the send route hands to Postmark.
    """

    user = _make_user(
        "sc-intake-body@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data=_SAMPLE_INTAKE_FORM,
    )

    captured: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        captured["body"] = args[2] if len(args) > 2 else kwargs.get("body")
        captured["html_body"] = kwargs.get("html_body")

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 200

    body = captured["body"]
    assert "BOOKING DETAILS" in body
    assert "Acme Tissue Bank" in body
    assert "Pat Consignee" in body
    assert "2026-07-01" in body
    assert "Page on arrival." in body

    html_body = captured["html_body"]
    assert "Acme Tissue Bank" in html_body
    assert "Booking details" in html_body


def test_sc_email_ops_email_body_omits_intake_block_when_empty(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unfilled intake must not ship an empty ``BOOKING DETAILS``
    header in the plain-text body.
    """

    user = _make_user(
        "sc-intake-skip@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    captured: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        captured["body"] = args[2] if len(args) > 2 else kwargs.get("body")

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 200
    assert "BOOKING DETAILS" not in captured["body"]


def test_sc_email_ops_intake_uppercases_state_and_zip(app: Flask) -> None:
    """State + ZIP fields normalize to uppercase at the parser
    boundary so downstream renderers (composer card + email body)
    don't carry mixed-case noise into ops's inbox.
    """

    from app.models import SCQuoteSession
    import json as _json

    user = _make_user(
        "sc-intake-case@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data={
            "shipper_state": "az",
            "shipper_zip": "m5v 3l9",
            "consignee_state": "wa",
            "consignee_zip": "98101-1234",
            "shipper_name": "Jane Doe",
        },
    )
    db.session.expire_all()
    persisted = SCQuoteSession.query.get(session.id)
    intake = _json.loads(persisted.booking_intake_json)
    assert intake["shipper"]["state"] == "AZ"
    assert intake["shipper"]["zip"] == "M5V 3L9"
    assert intake["consignee"]["state"] == "WA"
    assert intake["consignee"]["zip"] == "98101-1234"
    # Free-text fields are NOT uppercased (would be obnoxious in a name).
    assert intake["shipper"]["name"] == "Jane Doe"


def test_sc_email_ops_text_body_skips_empty_address_continuation(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a shipper or consignee block has a street address but no
    city/state/zip, the email body must NOT emit a trailing line of
    whitespace where the ``city, state zip`` continuation would
    otherwise sit. The conditional on the continuation line guards
    against that.
    """

    user = _make_user(
        "sc-intake-no-csz@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data={
            "shipper_name": "Whitespace Test",
            "shipper_street": "456 Mystery Lane",
            # city / state / zip intentionally omitted
            "consignee_name": "Also Whitespace",
            "consignee_street": "789 Unknown Rd",
        },
    )

    captured: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        captured["body"] = args[2] if len(args) > 2 else kwargs.get("body")

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 200
    body = captured["body"]
    # The street should appear, but the continuation indent (11
    # spaces from ``Address:   ``) followed by nothing-but-whitespace
    # must not.
    assert "456 Mystery Lane" in body
    assert "789 Unknown Rd" in body
    assert "           \n" not in body
    # And no run of trailing whitespace after the street line:
    assert "456 Mystery Lane\n           \n" not in body


def test_sc_email_ops_composer_escapes_intake_for_xss(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stored intake values rendered into the composer page must be
    HTML-escaped so a malicious shipper-notes string can't become
    stored XSS for the next user who opens
    ``/sc/quote/<id>/email-ops``.

    The Postmark plain-text body, by contrast, must NOT be escaped -
    the email body is literal text and ops should see the original
    characters in their inbox.
    """

    user = _make_user(
        "sc-intake-xss@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    payload = "<script>alert('xss')</script>"

    client = app.test_client()
    _login(client, user.id)
    client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data={
            "shipper_name": "Acme",
            "shipper_notes": payload,
            "consignee_name": "Recipient",
        },
    )

    composer_html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    # The raw payload must NOT survive on the composer page - it would
    # execute in the next viewer's browser.
    assert payload not in composer_html
    # ...but the escaped form must be present so ops still sees the
    # literal text rendered in the preview.
    assert "&lt;script&gt;alert" in composer_html

    # The Postmark plain-text body, in contrast, must keep the raw
    # characters - the body is literal text the mail client never
    # interprets as HTML.
    captured: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        captured["body"] = args[2] if len(args) > 2 else kwargs.get("body")

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 200
    assert payload in captured["body"]


def test_sc_email_ops_composer_html_preview_is_sandboxed_in_iframe(
    app: Flask,
) -> None:
    """Regression: the formatted-HTML email preview must be embedded
    via ``<iframe srcdoc>``, not inlined via ``{% include %}``.

    The email body template starts with ``<!doctype html>...<body
    style="background:#f8f9fa;...">`` so inlining it makes the
    browser merge the email's ``<body>`` attributes (including the
    light background) onto the composer page's single ``<body>``
    element, which paved over the page's dark-mode theme. The iframe
    sandboxes the embedded document so its inline styles can't
    escape into the parent.
    """

    user = _make_user(
        "sc-iframe@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)

    # The iframe carries the rendered email document via srcdoc and
    # the sandbox attribute keeps any scripts in the embedded body
    # from running.
    assert 'id="sc-email-html-preview"' in html
    assert 'srcdoc=' in html
    # ``allow-popups`` is required so a ``mailto:`` link inside the
    # rendered email body (the user's email address surfaces as one
    # in the preview header) can still open the user's mail client
    # when clicked from inside the sandboxed iframe.
    assert 'sandbox="allow-same-origin allow-popups"' in html

    # An inline ``{% include %}`` of the email template would leave
    # a SECOND raw ``<body`` opening tag in the composer page's
    # source (the email template starts with ``<!doctype html>`` and
    # opens its own ``<body style="background:#f8f9fa;...">``). The
    # outer base.html still contributes the page's own ``<body>``,
    # so what we want to assert is exactly one occurrence of an
    # unescaped opening body tag in the document. Anything inside the
    # iframe's ``srcdoc`` attribute is escaped to ``&lt;body...`` and
    # not counted.
    assert html.count("<body") == 1


def test_sc_email_ops_composer_buttons_warn_when_intake_missing(
    app: Flask,
) -> None:
    """All three composer action buttons must surface a warning
    (yellow class + ⚠ icon + Bootstrap tooltip) when the SC user has
    not entered shipper / consignee details. The tooltip text
    explicitly tells the user the booking email will not include the
    Booking Details section.
    """

    user = _make_user(
        "sc-warn-empty@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)

    # Every button id appears with the warning class + tooltip
    # attribute. ``Email to Book`` swaps from ``btn-primary`` to
    # ``btn-warning`` because the warning is the primary signal.
    for marker in (
        'id="sc-send-postmark-btn"',
        'id="sc-copy-body-btn"',
        'id="sc-mailto-btn"',
    ):
        # Find the start of the matching tag and extract enough
        # characters to cover the attribute list.
        idx = html.find(marker)
        assert idx != -1, f"{marker} not in composer HTML"
        chunk = html[idx : idx + 600]
        assert 'data-bs-toggle="tooltip"' in chunk
        assert "Shipper / consignee details have not been entered" in chunk
        # The ⚠ prefix appears as part of the button label too.
    assert "⚠ Email to Book" in html
    assert "⚠ Copy body" in html
    assert "⚠ Open in mail client" in html


def test_sc_email_ops_composer_buttons_drop_warning_when_intake_populated(
    app: Flask,
) -> None:
    """Once the intake is filled in, the three buttons revert to
    their normal styling and no tooltip is rendered.
    """

    user = _make_user(
        "sc-warn-filled@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    client.post(
        f"/sc/quote/{session.id}/email-ops/intake",
        data={
            "shipper_name": "Acme Tissue Bank",
            "consignee_name": "Recipient Lab",
            "pickup_date": "2026-07-01",
        },
    )

    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    # Tooltips and warning labels are gone.
    assert "Shipper / consignee details have not been entered" not in html
    assert "⚠ Email to Book" not in html
    # Default styling restored: send button is ``btn-primary`` again.
    idx = html.find('id="sc-send-postmark-btn"')
    chunk = html[idx - 200 : idx + 200]
    assert "btn-primary" in chunk
    assert "btn-warning" not in chunk


def test_sc_results_partial_links_to_intake_form() -> None:
    """The 'Email Ops for Booking' button in the SC results card
    routes through the new intake form rather than jumping straight
    to the composer page. Asserts on the template source - the
    partial has many context vars and rendering it standalone is
    overkill for a URL-routing check.
    """

    from pathlib import Path

    template = Path("templates/sc/_results_partial.html").read_text()
    assert "science_care.sc_email_ops_intake" in template
    # The old direct-to-composer route reference must NOT survive on
    # the button (other references to ``sc_email_ops_for_booking``
    # are fine).
    assert "sc_email_ops_for_booking" not in template


def test_sc_email_ops_text_body_separates_consecutive_legs(
    app: Flask,
) -> None:
    """The plain-text booking email must insert a visible separator
    line between consecutive legs so a multi-leg shipment doesn't
    read as one continuous block. The separator appears between legs
    only - never before the first leg or after the last leg (where
    the ``GRAND TOTAL`` divider already provides closure).
    """

    from app.models import Quote, SCQuoteSession, SCQuoteSessionLeg
    import json

    user = _make_user(
        "sc-multi-leg-sep@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session = SCQuoteSession(
        user_id=user.id,
        grand_total=600.0,
        payload_json=json.dumps({"multi_reference": "SCMQ0700"}),
        multi_reference="SCMQ0700",
    )
    db.session.add(session)
    db.session.flush()
    for idx in range(1, 4):
        quote = Quote(
            user_id=user.id,
            user_email="sc-buyer@example.com",
            quote_type="Air",
            origin=f"7501{idx}",
            destination="80205",
            weight=225.0,
            pieces=1,
            zone="X",
            total=200.0,
            quote_metadata="{}",
            rate_set=RATE_SET_SCIENCE_CARE,
        )
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            SCQuoteSessionLeg(
                session_id=session.id,
                leg_index=idx,
                air_quote_id=quote.id,
                winner_mode="Air",
                winner_total=200.0,
            )
        )
    db.session.commit()

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)

    separator = "-" * 97
    # Three legs -> exactly two separators between them.
    assert html.count(separator) == 2
    # The separator must never appear immediately before "Leg 1:" -
    # only between consecutive legs.
    pre_leg1 = html.split("Leg 1:", 1)[0]
    assert separator not in pre_leg1
    # The separator carries blank lines on BOTH sides for readability -
    # a regression that dropped the leading blank line would still pass
    # the count assertion, so check the surrounding whitespace
    # explicitly.
    assert f"\n\n{separator}\n\n" in html


def test_sc_email_ops_send_to_self_dispatches_only_to_logged_in_user(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composer's ``Send to Myself`` POST must call send_email
    with the requesting user as the ``To`` address only - no ``Cc``,
    and especially NOT to the ops inbox - and persist a ``sent``
    BookingEmailReceipt tagged ``sc_multi_self``.

    The body templates are re-used across both send paths, so the
    intake and HTML body checks are still relevant; the new contract
    is the recipient list and the audit kind.
    """

    from app.models import BookingEmailReceipt

    user = _make_user(
        "sc-self@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    captured: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        captured["to"] = args[0] if args else kwargs.get("to")
        captured["subject"] = args[1] if len(args) > 1 else kwargs.get("subject")
        captured["body"] = args[2] if len(args) > 2 else kwargs.get("body")
        captured.update(kwargs)

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        f"/sc/quote/{session.id}/email-ops/send-to-self"
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["status"] == "sent"
    # The logged-in user is the SOLE recipient on a self-send.
    assert payload["to_addr"] == "sc-self@example.com"
    assert payload["cc_addr"] is None
    assert "Preview" in payload["subject"]
    assert "SCMQ0500" in payload["subject"]

    # send_email contract: ``To`` is the user, headers carry no Cc
    # (or no headers at all), the feature label distinguishes from
    # the ops send so per-feature rate limits don't conflate them.
    assert captured["to"] == "sc-self@example.com"
    assert captured["feature"] == "sc_booking_email_self"
    assert not captured.get("headers")
    # Body templates are identical to the ops path - sanity-check
    # the headline copy + the HTML alternative are present.
    assert "SCIENCE CARE MULTI-LEG BOOKING REQUEST" in captured["body"]
    assert captured["html_body"]

    receipt = BookingEmailReceipt.query.filter_by(
        sender_user_id=user.id
    ).one()
    assert receipt.kind == "sc_multi_self"
    assert receipt.reference == "SCMQ0500"
    assert receipt.status == "sent"
    assert receipt.to_addr == "sc-self@example.com"
    assert receipt.cc_addr is None


def test_sc_email_ops_send_to_self_400s_when_user_has_no_email(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user with no email address on file can't get a self-copy;
    surface that as a clean 400 instead of attempting an empty
    ``To:`` send.
    """

    user = User(
        email="will-be-cleared@example.com",
        name="No Email Person",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(user)
    db.session.commit()
    session, _ = _seed_sc_session_with_legs(user.id)

    # Now nuke the address so the route sees an empty user_email.
    # ``email`` is unique-indexed but accepts a single empty string;
    # set it directly here, commit, then expire so the route's
    # ``current_user`` reads the updated value.
    user.email = ""
    db.session.commit()
    db.session.expire_all()

    def _explode(*args, **kwargs) -> None:
        raise AssertionError("send_email should not be called when user has no email")

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _explode
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        f"/sc/quote/{session.id}/email-ops/send-to-self"
    )
    assert response.status_code == 400
    assert response.get_json()["status"] == "failed"


def test_sc_email_ops_send_to_self_blocks_non_sc_user(app: Flask) -> None:
    """A logged-in non-SC user gets the same 403 the regular send
    route returns.
    """

    sc_user = _make_user(
        "sc-self-owner@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(sc_user.id)
    non_sc = _make_user("not-sc-self@example.com", rate_set="default")
    client = app.test_client()
    _login(client, non_sc.id)
    response = client.post(
        f"/sc/quote/{session.id}/email-ops/send-to-self"
    )
    assert response.status_code == 403


def test_sc_email_ops_composer_renders_send_to_self_button(
    app: Flask,
) -> None:
    """The composer must render the new ``Send to Myself`` button
    with the correct POST URL and the explanatory copy in the
    "Heads up" alert.
    """

    user = _make_user(
        "sc-self-ui@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    assert 'id="sc-send-to-self-btn"' in html
    assert f"/sc/quote/{session.id}/email-ops/send-to-self" in html
    assert "Send to Myself" in html


def test_sc_email_ops_composer_disables_send_to_self_when_user_has_no_email(
    app: Flask,
) -> None:
    """A user with no email on file gets a disabled ``Send to Myself``
    button with an explanatory tooltip, so they don't trip over the
    400 the POST route would otherwise return. The ops ``Email to
    Book`` button stays clickable because it doesn't depend on
    user_email.
    """

    user = User(
        email="will-be-cleared-ui@example.com",
        name="No Email Person UI",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
    )
    db.session.add(user)
    db.session.commit()
    session, _ = _seed_sc_session_with_legs(user.id)
    user.email = ""
    db.session.commit()
    db.session.expire_all()

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    # Locate the Send-to-Myself button's tag and inspect its
    # attribute list directly so an unrelated ``disabled`` elsewhere
    # on the page can't satisfy the assertion.
    idx = html.find('id="sc-send-to-self-btn"')
    assert idx != -1
    chunk = html[idx : idx + 600]
    assert "disabled" in chunk
    assert "no email address on file" in chunk
    # The ops Email-to-Book button doesn't depend on user_email - the
    # backend just skips the Cc when the user has no address - so it
    # must NOT be disabled here.
    idx_ops = html.find('id="sc-send-postmark-btn"')
    chunk_ops = html[idx_ops : idx_ops + 600]
    assert "disabled" not in chunk_ops


def test_sc_email_ops_send_dispatches_via_postmark_and_records_receipt(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composer's ``Email to Book`` POST must call send_email with
    the ops To address + the logged-in user's CC, render both an HTML
    and a plain-text body, persist a ``sent`` BookingEmailReceipt
    row, and return the receipt fields as JSON for the composer UI.
    """

    from app.models import BookingEmailReceipt

    user = _make_user(
        "sc-postmark@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    captured: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        # Mirror the positional + keyword shape of the call site so the
        # assertions document the contract instead of the call style.
        captured["to"] = args[0] if args else kwargs.get("to")
        captured["subject"] = args[1] if len(args) > 1 else kwargs.get("subject")
        captured["body"] = args[2] if len(args) > 2 else kwargs.get("body")
        captured.update(kwargs)

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["status"] == "sent"
    assert payload["to_addr"] == "operations@freightservices.net"
    assert payload["cc_addr"] == "sc-postmark@example.com"
    assert payload["subject"].startswith("SC Multi-leg Booking Request")
    assert "SCMQ0500" in payload["subject"]

    # send_email contract: To is ops, body is the rendered plain-text
    # body, html_body is the rendered HTML template, Cc rides on the
    # headers mapping so the SMTP envelope picks it up.
    assert captured["to"] == "operations@freightservices.net"
    assert "SCIENCE CARE MULTI-LEG BOOKING REQUEST" in captured["body"]
    assert "Shipment weight summary" in captured["body"]
    assert captured["feature"] == "sc_booking_email"
    assert captured["html_body"]
    assert "<table" in captured["html_body"]
    assert "Shipment weight summary" in captured["html_body"]
    assert captured["headers"] == {"Cc": "sc-postmark@example.com"}

    receipt = BookingEmailReceipt.query.filter_by(
        sender_user_id=user.id
    ).one()
    assert receipt.kind == "sc_multi"
    assert receipt.reference == "SCMQ0500"
    assert receipt.status == "sent"
    assert receipt.to_addr == "operations@freightservices.net"
    assert receipt.cc_addr == "sc-postmark@example.com"
    assert receipt.error_text is None


def test_sc_email_ops_send_persists_failed_receipt_on_smtp_error(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the underlying SMTP send raises, the route must persist a
    ``failed`` receipt (so ops have a paper trail of the attempt) and
    return a JSON error so the composer banner can surface a fallback
    instruction.
    """

    from app.models import BookingEmailReceipt

    user = _make_user(
        "sc-postmark-fail@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    def _fake_send_email(*args, **kwargs) -> None:
        raise RuntimeError("Postmark 500: server unreachable")

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["status"] == "failed"
    assert "fallback" in payload["message"].lower()

    receipt = BookingEmailReceipt.query.filter_by(
        sender_user_id=user.id
    ).one()
    assert receipt.status == "failed"
    assert "Postmark 500" in (receipt.error_text or "")


def test_sc_email_ops_send_blocks_non_sc_user(app: Flask) -> None:
    """A logged-in non-SC user must be rejected by sc_user_required."""

    sc_user = _make_user(
        "sc-owner-send@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(sc_user.id)
    non_sc = _make_user("not-sc-send@example.com", rate_set="default")
    client = app.test_client()
    _login(client, non_sc.id)
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 403


def test_sc_email_ops_preview_includes_user_company_name(
    app: Flask,
) -> None:
    """The SC composer ``Requested by`` line must carry the user's
    ``company_name`` column.

    Regression: an earlier revision read ``getattr(..., "company")``,
    which silently resolved to ``""`` and dropped the company from
    every booking email. Asserting against a distinctive string is
    enough to lock the contract.
    """

    user = User(
        email="sc-co@example.com",
        name="Casey SC",
        password_hash="x",
        rate_set=RATE_SET_SCIENCE_CARE,
        company_name="ScienceCare Logistics Co",
    )
    db.session.add(user)
    db.session.commit()
    session, _ = _seed_sc_session_with_legs(user.id)

    client = app.test_client()
    _login(client, user.id)
    html = client.get(
        f"/sc/quote/{session.id}/email-ops"
    ).get_data(as_text=True)
    assert "ScienceCare Logistics Co" in html


def test_sc_email_ops_send_rolls_back_poisoned_session_on_failure(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``send_email`` raises after the rate-limit log half-committed
    (i.e. the session is in a poisoned state), the route's failure
    path must still record ``status=failed`` instead of choking on a
    ``PendingRollbackError``.

    This simulates the worst case by having the mock open a partial
    write + raise, then asserting the audit row reflects the failure.
    """

    from app.models import BookingEmailReceipt
    from sqlalchemy.exc import OperationalError

    user = _make_user(
        "sc-poisoned@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    def _fake_send_email(*args, **kwargs) -> None:
        # Force the session into the "transaction has been rolled back
        # due to a previous exception" state that
        # ``log_email_dispatch``'s commit would trigger if Postmark
        # were unreachable mid-transaction. Picking a real
        # SQLAlchemy error so we exercise the rollback code path the
        # production failure mode hits.
        db.session.execute(
            __import__("sqlalchemy").text("SELECT 1 FROM nonexistent_table")
        )

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 502, response.get_data(as_text=True)

    # The audit row must reflect the failure, not be lost to a
    # PendingRollbackError - that's the regression contract.
    receipt = BookingEmailReceipt.query.filter_by(
        sender_user_id=user.id
    ).one()
    assert receipt.status == "failed"
    assert receipt.error_text


def test_sc_email_ops_send_persists_pending_row_before_send_email(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash inside ``send_email`` must still leave an audit row.

    The route persists a ``pending`` BookingEmailReceipt before the
    network call so a kill-9'd worker can't lose the attempt. This
    test verifies the pre-send commit happens by inspecting the DB
    from inside the ``send_email`` mock: at that point the row must
    already exist with ``status=pending``.
    """

    from app.models import BookingEmailReceipt

    user = _make_user(
        "sc-pending@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session, _ = _seed_sc_session_with_legs(user.id)

    observed: dict[str, object] = {}

    def _fake_send_email(*args, **kwargs) -> None:
        # Snapshot the audit row state mid-send so the assertion runs
        # before the route's post-send commit overwrites ``pending``.
        row = BookingEmailReceipt.query.filter_by(
            sender_user_id=user.id
        ).one()
        observed["status_during_send"] = row.status
        observed["committed_id"] = row.id

    monkeypatch.setattr(
        "app.science_care.routes.send_email", _fake_send_email
    )

    client = app.test_client()
    _login(client, user.id)
    response = client.post(f"/sc/quote/{session.id}/email-ops/send")
    assert response.status_code == 200

    assert observed["status_during_send"] == "pending"
    final = BookingEmailReceipt.query.get(observed["committed_id"])
    assert final.status == "sent"


def test_sc_email_ops_blocks_non_sc_user(app: Flask) -> None:
    sc_user = _make_user(
        "sc-owner@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    session = _seed_sc_session(sc_user.id)
    non_sc = _make_user("not-sc@example.com", rate_set="default")
    client = app.test_client()
    _login(client, non_sc.id)
    response = client.get(f"/sc/quote/{session.id}/email-ops")
    # sc_user_required must short-circuit before the view loads.
    assert response.status_code in {302, 403}


def test_sc_lookup_get_renders_empty_form_without_500(app: Flask) -> None:
    """GET /sc/quote/lookup must render the empty form for any logged-in
    SC user. Regression for a Jinja shadowing trap where the template's
    ``{% if session %}`` guard fell through to Flask's global ``session``
    (always truthy for an authenticated user) and 500'd trying to read
    ``session.id`` on the lookup-result block.
    """

    user = _make_user(
        "sc-lookup-get@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote/lookup")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Form is present...
    assert 'name="multi_reference"' in html
    # ...and the result block is NOT rendered (no card / table headers).
    assert "Multi-leg quote summary" not in html


def test_sc_lookup_resolves_session_across_users(app: Flask) -> None:
    """Any SC user can look up any SCMQ reference.

    This is intentional - the lookup is how a customer-service SC user
    helps a customer find the multi-leg quote they generated. A future
    PR can tighten this if a privacy review demands per-user scope.
    """

    owner = _make_user("sc-owner-2@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    _seed_sc_session(owner.id, multi_reference="SCMQ0007")
    # Different SC user (no role escalation, no admin flag).
    other = _make_user("sc-helper@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, other.id)
    response = client.post(
        "/sc/quote/lookup",
        data={"multi_reference": "scmq0007"},  # case-insensitive
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "SCMQ0007" in html
    # The looked-up summary shows the persisted grand total.
    assert "$250.00" in html


def test_sc_lookup_unknown_reference_flashes_warning(app: Flask) -> None:
    user = _make_user("sc-lookup-miss@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/sc/quote/lookup",
        data={"multi_reference": "SCMQNOPE"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No multi-leg quote found" in html


def test_sc_quote_form_prefills_from_session(app: Flask) -> None:
    """GET ``/sc/quote?from_session=<id>`` prefills inputs from a prior session.

    Exercises the full SC prefill plumbing: simple per-leg fields, the
    is_return checkbox, the multi_reference being stripped, the tissue
    row count expanding from 1 to N, and the synthetic LegResult
    threading through the box-count + consumable Qty grids.
    """

    import json as _json

    from app.models import (
        SCAccessorialMap,
        SCBoxType,
        SCConsumable,
        SCLab,
        SCQuoteSession,
        SCTissueCode,
    )

    user = _make_user("sc-prefill@example.com", rate_set=RATE_SET_SCIENCE_CARE)
    lab = SCLab(
        lab_code="SCCA",
        lab_name="Tucson",
        origin_zip="85705",
        is_active=True,
    )
    box = SCBoxType(
        code="MED",
        label="Medium",
        length_in=20,
        width_in=15,
        height_in=18,
        tare_weight_lb=4.0,
    )
    tissue = SCTissueCode(
        tissue_code="ARM01",
        description="Arm",
        unit_weight_lb=12.0,
        default_box_type_code="MED",
        pieces_per_box=4,
    )
    cons = SCConsumable(
        consumable_type="dry_ice",
        temp_mode="frozen",
        scope="domestic",
        weight_lb_per_box=25.0,
    )
    acc_map = SCAccessorialMap(
        form_field="J3",
        display_label="4 Hour Delivery/Pick-Up Window",
        accessorial_name="4hr Window",
    )
    db.session.add_all([lab, box, tissue, cons, acc_map])
    db.session.commit()

    payload = {
        "multi_reference": "SCMQ0007",
        "lab_code_1": "SCCA",
        "dest_zip_1": "98101",
        "routing_type_1": "Outbound",
        "temp_mode_1": "frozen",
        "intl_country_1": "",
        "is_return_1": "Y",
        f"acc_J3_1": "Y",
        "tissue_code_1_1": "ARM01",
        "qty_1_1": "5",
        "tissue_code_1_2": "ARM01",
        "qty_1_2": "3",
        f"box_count_1_{box.id}": "2",
        f"cons_qty_1_{cons.id}": "4",
    }
    session = SCQuoteSession(
        user_id=user.id,
        grand_total=0.0,
        payload_json=_json.dumps(payload),
        multi_reference="SCMQ0007",
    )
    db.session.add(session)
    db.session.commit()
    db.session.refresh(session)

    client = app.test_client()
    _login(client, user.id)
    response = client.get(f"/sc/quote?from_session={session.id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Simple per-leg fields landed.
    assert 'name="lab_code_1"' in html and 'value="SCCA"' in html
    assert 'name="dest_zip_1"' in html and 'value="98101"' in html
    # routing_type select picked the right option.
    assert "<option selected>Outbound</option>" in html or (
        '<option selected="">Outbound</option>' in html
    ) or (
        'value="Outbound" selected' in html
    ) or (
        'Outbound</option>' in html and 'selected>Outbound' in html
    ) or (
        'selected\n                >Outbound' in html
    )
    # temp_mode select prefilled to frozen.
    assert 'value="frozen"' in html
    # is_return checkbox checked.
    assert 'id="is_return_1"' in html
    # acc_J3_1 checkbox checked.
    assert 'name="acc_J3_1"' in html and "checked" in html
    # Two tissue rows expanded for leg 1 (i=1 and i=2).
    assert 'name="tissue_code_1_1"' in html
    assert 'name="tissue_code_1_2"' in html
    assert 'name="qty_1_1"' in html and 'value="5"' in html
    assert 'name="qty_1_2"' in html and 'value="3"' in html
    # Box-count override projected back into the grid.
    assert f'name="box_count_1_{box.id}"' in html
    # Consumable qty override projected back into the grid.
    assert f'name="cons_qty_1_{cons.id}"' in html
    # Multi-reference is NOT prefilled - the new submission needs a
    # fresh ref to avoid the UNIQUE collision.
    assert 'value="SCMQ0007"' not in html


def test_sc_quote_form_prefill_missing_session_flashes_warning(
    app: Flask,
) -> None:
    """An unknown ``?from_session=`` renders the empty form with a warning."""

    user = _make_user(
        "sc-prefill-miss@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    client = app.test_client()
    _login(client, user.id)
    response = client.get("/sc/quote?from_session=999999")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Could not find the multi-leg quote" in html


def test_edit_quote_sc_ref_dispatches_to_sc_form(app: Flask) -> None:
    """An ``SCMQNNNN`` reference at ``/quotes/edit`` routes to ``/sc/quote``."""

    import json as _json
    from app.models import SCQuoteSession

    user = _make_user(
        "edit-sc@example.com", rate_set=RATE_SET_SCIENCE_CARE
    )
    sc_session = SCQuoteSession(
        user_id=user.id,
        grand_total=0.0,
        payload_json=_json.dumps({"lab_code_1": "SCCA"}),
        multi_reference="SCMQ0099",
    )
    db.session.add(sc_session)
    db.session.commit()
    db.session.refresh(sc_session)

    client = app.test_client()
    _login(client, user.id)
    response = client.post(
        "/quotes/edit", data={"client_reference": "SCMQ0099"}
    )
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/sc/quote" in location
    assert f"from_session={sc_session.id}" in location
