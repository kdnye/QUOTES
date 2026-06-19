"""Tests for the Jinja globals + filters registered by create_app.

Covers the two helpers added in audit PR-D:

* ``csrf_input()`` — emits a hidden CSRF input. Replaced 35+ inline
  copies across 25 templates.
* ``currency`` filter — formats numbers as ``$X,XXX.XX``. Replaced
  25+ inline ``${{ '%.2f'|format(...) }}`` instances.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask, render_template_string

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402


class TestTemplateHelpersConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    TestTemplateHelpersConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestTemplateHelpersConfig)
    with app.app_context():
        yield app
        db.session.remove()
        db.metadata.reflect(bind=db.engine)
        db.drop_all()


def test_csrf_input_emits_hidden_input(app: Flask) -> None:
    with app.test_request_context():
        rendered = render_template_string("{{ csrf_input() }}")
    assert 'type="hidden"' in rendered
    assert 'name="csrf_token"' in rendered
    assert "value=" in rendered
    # Returns Markup so the HTML is NOT double-escaped.
    assert "&lt;input" not in rendered


def test_currency_filter_formats_with_thousands_separator(
    app: Flask,
) -> None:
    with app.test_request_context():
        rendered = render_template_string("{{ 1234567.5 | currency }}")
    assert rendered.strip() == "$1,234,567.50"


def test_currency_filter_zero(app: Flask) -> None:
    with app.test_request_context():
        rendered = render_template_string("{{ 0 | currency }}")
    assert rendered.strip() == "$0.00"


def test_currency_filter_none_returns_empty_string(app: Flask) -> None:
    # Callers using `{{ q.total or 0 | currency }}` rely on the
    # short-circuit; tests pin the None case directly to make sure
    # nothing inside the filter blows up.
    with app.test_request_context():
        rendered = render_template_string("{{ None | currency }}")
    assert rendered.strip() == ""


def test_currency_filter_string_input_is_coerced(app: Flask) -> None:
    with app.test_request_context():
        rendered = render_template_string("{{ '42.5' | currency }}")
    assert rendered.strip() == "$42.50"


def test_currency_filter_garbage_returns_empty_string(app: Flask) -> None:
    with app.test_request_context():
        rendered = render_template_string("{{ 'NaN-ish' | currency }}")
    assert rendered.strip() == ""


def test_currency_filter_places_kwarg(app: Flask) -> None:
    with app.test_request_context():
        rendered = render_template_string("{{ 3.14159 | currency(4) }}")
    assert rendered.strip() == "$3.1416"


def test_base_html_loads_htmx_csrf_wiring() -> None:
    # Path-based check so the regression doesn't depend on a real
    # render - the HTMX CSRF wiring is critical for every HTMX page
    # and must not be accidentally removed when someone tidies the
    # script block.
    contents = (
        PROJECT_ROOT / "templates" / "base.html"
    ).read_text()
    assert "htmx:configRequest" in contents
    assert "X-CSRFToken" in contents
