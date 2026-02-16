from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.help import help_bp


class _AnonymousUser:
    """Minimal user object for templates that check authentication flags.

    Args:
        None.

    Attributes:
        is_authenticated: Always ``False`` for anonymous test requests.

    External dependencies:
        * Mirrors the ``current_user`` interface provided by Flask-Login.
    """

    is_authenticated = False


def _build_help_test_app() -> Flask:
    """Create a minimal Flask app that can render help templates for route tests.

    Args:
        None.

    Returns:
        Flask: App instance with the help blueprint and template directory.

    External dependencies:
        * Registers :data:`app.help.help_bp` on a test Flask application.
    """

    template_root = Path(__file__).resolve().parents[1] / "templates"
    app = Flask("help-routes-test", template_folder=str(template_root))
    app.secret_key = "test-secret-key"

    @app.context_processor
    def inject_theme_helper() -> dict[str, object]:
        """Provide template helpers expected by ``templates/base.html``.

        Args:
            None.

        Returns:
            dict[str, object]: Context values consumed by base templates.

        External dependencies:
            * Mimics the ``fsi_theme`` helper injected by
              :func:`app.quote.theme.init_fsi_theme` in the production app.
        """

        return {"fsi_theme": lambda: "", "current_user": _AnonymousUser()}

    app.register_blueprint(help_bp, url_prefix="/help")
    return app


def test_help_emailing_route_renders_workflow_content() -> None:
    """Serve the emailing guide and include workflow-specific headings.

    Args:
        None.

    Returns:
        None. Assertions verify rendered content from the help template.

    External dependencies:
        * Calls ``help.emailing_guide`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()

    response = client.get("/help/emailing")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Email Request Workflow" in html
    assert "Quote Details" in html
    assert "Shipment Specifications" in html
    assert "Pricing Breakdown" in html
    assert "Logistics Contacts" in html
    assert "Actual" in html
    assert "Dimensional" in html
    assert "operations@freightservices.net" in html


def test_help_index_links_to_emailing_guide() -> None:
    """Expose a discoverable link to the emailing guide from help index.

    Args:
        None.

    Returns:
        None. Assertions verify the index page includes a guide link.

    External dependencies:
        * Calls ``help.help_index`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()

    response = client.get("/help/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Email Request Workflow guide" in html
    assert 'href="/help/emailing"' in html
