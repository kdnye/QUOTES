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
