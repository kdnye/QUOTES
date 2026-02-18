from __future__ import annotations

from pathlib import Path

from flask import Flask, render_template_string
import pytest

from app.quote import theme as theme_module


def test_init_fsi_theme_skips_blueprint_when_assets_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure theme blueprint is skipped and helper is safe without assets.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest fixture for runtime attribute overrides.

    External Dependencies:
        * Overrides :data:`app.quote.theme.bp.static_folder` via
          :func:`monkeypatch.setattr`.
        * Calls :func:`app.quote.theme.init_fsi_theme`.
        * Renders the helper via :func:`flask.render_template_string`.
    """

    app = Flask("theme-missing-assets")
    monkeypatch.setattr(theme_module.bp, "static_folder", str(tmp_path))

    theme_module.init_fsi_theme(app)

    assert theme_module.bp.name not in app.blueprints

    with app.test_request_context():
        rendered = render_template_string("{{ fsi_theme() }}")
        assert rendered == ""


def test_init_fsi_theme_registers_blueprint_when_assets_exist() -> None:
    """Ensure theme blueprint and helper link are available with assets.

    Args:
        None.

    Returns:
        ``None``. Verifies behavior through assertions.

    External Dependencies:
        * Calls :func:`app.quote.theme.init_fsi_theme`.
        * Renders the helper via :func:`flask.render_template_string`.
    """

    app = Flask("theme-with-assets")

    theme_module.init_fsi_theme(app)

    assert theme_module.bp.name in app.blueprints

    with app.test_request_context():
        rendered = render_template_string("{{ fsi_theme() }}")
        assert "/theme/static/fsi.css" in rendered


def test_base_template_uses_branded_header_classes() -> None:
    """Verify base layout includes branding and theme toggle wiring.

    Args:
        None.

    Returns:
        ``None``. Verifies behavior through assertions.

    External Dependencies:
        * Reads ``templates/base.html`` with :class:`pathlib.Path`.
        * Reads ``app/theme/static/fsi.css`` with :class:`pathlib.Path`.
    """

    base_template = Path("templates/base.html").read_text()
    theme_css = Path("app/theme/static/fsi.css").read_text()

    assert 'class="navbar navbar-expand-lg fsi-navbar"' in base_template
    assert 'class="navbar-brand fsi-brand"' in base_template
    assert 'data-bs-theme="light"' in base_template
    assert 'id="darkModeToggle"' in base_template
    assert (
        'const systemThemeQuery = window.matchMedia("(prefers-color-scheme: dark)");'
        in base_template
    )
    assert 'applyTheme(systemThemeQuery.matches ? "dark" : "light");' in base_template
    assert 'systemThemeQuery.addEventListener("change", applySystemTheme);' in base_template
    assert 'let userOverride = false;' in base_template
    assert 'userOverride = true;' in base_template
    assert 'href="/quotes/new">Get New Quote</a>' in base_template
    assert '[data-bs-theme="dark"] {' in theme_css
    assert "@media (prefers-color-scheme: dark)" not in theme_css
    assert ".fsi-brand__lockup" in theme_css
    assert ".fsi-nav-link--cta" in theme_css
    assert ".btn.fsi-button-primary" in theme_css
    assert ".btn.fsi-button-secondary" in theme_css
    assert '[data-bs-theme="dark"] .fsi-notice--info' not in theme_css
    assert '[data-bs-theme="dark"] {\n' in theme_css
    assert "    .fsi-summary__label" in theme_css
