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
