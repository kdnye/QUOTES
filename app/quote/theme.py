"""Reusable theme blueprint and helpers for the Flask UI."""

from pathlib import Path
from typing import Callable, Dict

from flask import Blueprint, Flask, render_template_string, url_for

bp = Blueprint(
    "theme",
    __name__,
    static_folder=str(Path(__file__).resolve().parent.parent / "theme" / "static"),
    static_url_path="/theme/static",  # final URL will be /theme/static/…
)


def _theme_assets_available(static_dir: Path) -> bool:
    """Return whether the theme static directory and CSS asset exist.

    Args:
        static_dir: Filesystem directory expected to contain ``fsi.css``.

    Returns:
        ``True`` when the directory exists and contains ``fsi.css``.

    External Dependencies:
        * Reads from the filesystem via :meth:`pathlib.Path.exists` and
          :meth:`pathlib.Path.is_dir`.
    """

    return (
        static_dir.exists()
        and static_dir.is_dir()
        and (static_dir / "fsi.css").exists()
    )


def init_fsi_theme(app: Flask) -> None:
    """Register the theme blueprint and a Jinja helper.

    Args:
        app: Flask application instance that will receive the blueprint and helper.

    Returns:
        ``None``. The function registers a blueprint and context processor.

    External Dependencies:
        * Registers a blueprint via :meth:`flask.Flask.register_blueprint`.
        * Adds a context processor via :meth:`flask.Flask.context_processor`.
        * The helper calls :func:`flask.url_for` and
          :func:`flask.render_template_string` when assets are present.

    Usage in templates: ``{{ fsi_theme() }}``
    Or include the CSS link directly: ``<link rel="stylesheet" href="{{ url_for('theme.static', filename='fsi.css') }}">``
    """

    static_dir = Path(bp.static_folder or "")
    assets_available = _theme_assets_available(static_dir)

    # Avoid double-registration during tests or repeated calls.
    if assets_available and bp.name not in app.blueprints:
        app.register_blueprint(bp)

        @app.context_processor
        def _fsi_theme_helper() -> Dict[str, Callable[[], str]]:
            """Provide the ``fsi_theme`` helper for templates.

            Returns:
                Mapping with the ``fsi_theme`` helper used in templates.

            External Dependencies:
                * The helper calls :func:`flask.url_for` and
                  :func:`flask.render_template_string` when assets exist.
            """

            def fsi_theme() -> str:
                """Return the theme link tag or an empty string.

                Returns:
                    ``str`` containing a ``<link>`` tag for the theme CSS, or
                    ``""`` when assets are missing.

                External Dependencies:
                    * Calls :func:`flask.url_for` and
                      :func:`flask.render_template_string` when assets exist.
                """

                if not assets_available:
                    return ""

                href = url_for("theme.static", filename="fsi.css")
                # returns a <link> tag you can include in base.html
                return render_template_string(
                    '<link rel="stylesheet" href="{{ href }}">', href=href
                )

            return {"fsi_theme": fsi_theme}

    if not assets_available:

        @app.context_processor
        def _fsi_theme_helper() -> Dict[str, Callable[[], str]]:
            """Provide the ``fsi_theme`` helper for templates.

            Returns:
                Mapping with the ``fsi_theme`` helper used in templates.

            External Dependencies:
                * None. Returns a no-op helper when assets are absent.
            """

            def fsi_theme() -> str:
                """Return an empty string when theme assets are missing.

                Returns:
                    ``""`` to avoid broken links in templates.

                External Dependencies:
                    * None.
                """

                return ""

            return {"fsi_theme": fsi_theme}
