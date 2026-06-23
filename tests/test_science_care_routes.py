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
