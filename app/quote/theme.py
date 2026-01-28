"""Reusable theme blueprint and helpers for the Flask UI."""

from pathlib import Path
from typing import Optional

from flask import Blueprint, render_template_string, url_for


def _resolve_theme_static_folder(current_file: Optional[Path] = None) -> Path:
    """Resolve the filesystem path to the theme static assets.

    Args:
        current_file: Optional path to ``theme.py`` for test overrides. Defaults
            to :data:`__file__` when omitted.

    Returns:
        Path: Path to the ``theme/static`` directory used by the blueprint.

    External dependencies:
        * Uses :class:`pathlib.Path` to resolve filesystem paths.
    """

    module_path = (current_file or Path(__file__)).resolve()
    app_root = module_path.parents[1]
    repo_root = app_root.parent
    repo_candidate = repo_root / "theme" / "static"
    app_candidate = app_root / "theme" / "static"

    if repo_candidate.exists():
        return repo_candidate
    if app_candidate.exists():
        return app_candidate
    return repo_candidate


bp = Blueprint(
    "theme",
    __name__,
    static_folder=str(_resolve_theme_static_folder()),
    static_url_path="/theme/static",  # final URL will be /theme/static/…
)


def init_fsi_theme(app):
    """Register the theme blueprint and a Jinja helper.

    Usage in templates: ``{{ fsi_theme() }}``
    Or include the CSS link directly: ``<link rel="stylesheet" href="{{ url_for('theme.static', filename='fsi.css') }}">``
    """

    # Avoid double-registration during tests or repeated calls.
    if bp.name not in app.blueprints:
        app.register_blueprint(bp)

        @app.context_processor
        def _fsi_theme_helper():
            def fsi_theme():
                href = url_for("theme.static", filename="fsi.css")
                # returns a <link> tag you can include in base.html
                return render_template_string(
                    '<link rel="stylesheet" href="{{ href }}">', href=href
                )

            return {"fsi_theme": fsi_theme}
