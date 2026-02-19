from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Flask
from flask_login import LoginManager, UserMixin, login_user

from app.help import help_bp


class _StubUser(UserMixin):
    """Simple authenticated user used by help-route tests.

    Args:
        user_id: Stable identifier returned by :meth:`flask_login.UserMixin.get_id`.
        email: Email address used by :func:`app.help.is_internal_employee`.
        role: Role value read by :func:`app.help.is_internal_employee`.
        employee_approved: Approval flag used for employee-only checks.

    External dependencies:
        * Mirrors required fields from :class:`app.models.User` consumed by
          :func:`app.help.is_internal_employee`.
    """

    def __init__(
        self,
        user_id: str,
        email: str,
        role: str,
        employee_approved: bool,
    ) -> None:
        self.id = user_id
        self.email = email
        self.role = role
        self.employee_approved = employee_approved
        self.is_admin = role == "super_admin"


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

    login_manager = LoginManager()
    login_manager.init_app(app)

    users = {
        "customer": _StubUser("customer", "customer@example.com", "customer", False),
        "employee": _StubUser(
            "employee",
            "agent@freightservices.net",
            "employee",
            True,
        ),
        "domain_customer": _StubUser(
            "domain_customer",
            "ops@freightservices.net",
            "customer",
            False,
        ),
    }

    @login_manager.user_loader
    def load_user(user_id: str) -> _StubUser | None:
        """Return a test user for Flask-Login session restoration.

        Args:
            user_id: Identifier stored in the signed session cookie.

        Returns:
            Matching :class:`_StubUser` when present; otherwise ``None``.

        External dependencies:
            * Used by :class:`flask_login.LoginManager` while loading
              :data:`flask_login.current_user`.
        """

        return users.get(user_id)

    auth_bp = Blueprint("auth", __name__)

    @auth_bp.get("/login")
    def login() -> str:
        """Provide a placeholder login route for redirect assertions.

        Args:
            None.

        Returns:
            Static string response consumed by tests that do not authenticate.

        External dependencies:
            * Matches the endpoint name expected by :func:`app.policies.roles_required`.
        """

        return "login"

    @auth_bp.get("/settings")
    def settings() -> str:
        """Return a placeholder account settings response for base nav links."""

        return "settings"

    @auth_bp.get("/reset-request")
    def reset_request() -> str:
        """Return a placeholder password reset response for base nav links."""

        return "reset"

    @auth_bp.get("/logout")
    def logout() -> str:
        """Return a placeholder logout response for base nav links."""

        return "logout"

    @auth_bp.get("/login-as/<user_key>")
    def login_as(user_key: str) -> str:
        """Authenticate one of the in-memory users for a test request flow.

        Args:
            user_key: Key from the ``users`` mapping above.

        Returns:
            ``"ok"`` after calling :func:`flask_login.login_user`.

        External dependencies:
            * Calls :func:`flask_login.login_user` to set the authenticated
              session consumed by :data:`flask_login.current_user`.
        """

        login_user(users[user_key])
        return "ok"

    app.register_blueprint(auth_bp, url_prefix="/auth")

    admin_bp = Blueprint("admin", __name__)

    @admin_bp.get("/dashboard")
    def dashboard() -> str:
        """Return a placeholder admin dashboard response for nav links."""

        return "dashboard"

    app.register_blueprint(admin_bp, url_prefix="/admin")

    admin_quotes_bp = Blueprint("admin_quotes", __name__)

    @admin_quotes_bp.get("/quotes")
    def quotes_html() -> str:
        """Return a placeholder admin quote listing response for nav links."""

        return "quotes"

    app.register_blueprint(admin_quotes_bp, url_prefix="/admin")

    quotes_bp = Blueprint("quotes", __name__)

    @quotes_bp.get("/lookup")
    def lookup_quote() -> str:
        """Return a placeholder quote lookup response for nav links."""

        return "lookup"

    app.register_blueprint(quotes_bp, url_prefix="/quotes")

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

        return {"fsi_theme": lambda: "", "csrf_token": lambda: "test-csrf-token"}

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
    client.get("/auth/login-as/employee")

    response = client.get("/help/emailing")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Email Request Workflow" in html
    assert "Quote Details" in html
    assert "Shipment Specifications" in html
    assert "Pricing Breakdown" in html
    assert "Shipment Details" in html
    assert "Actual" in html
    assert "Dimensional" in html
    assert "operations@freightservices.net" in html


def test_help_index_renders_structured_sections() -> None:
    """Render the updated help portal sections for authenticated users.

    Args:
        None.

    Returns:
        None. Assertions verify the five accordion sections render correctly.

    External dependencies:
        * Calls ``help.help_index`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()
    client.get("/auth/login-as/employee")

    response = client.get("/help/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Shipment Calculation Guide" in html
    assert "User Account & Security" in html
    assert "Booking & Operations" in html
    assert "Legal & Privacy Policy" in html
    assert "Self-Service Quote Tool User Guide" in html
    assert "Operations booking fee" in html
    assert "Read Full Terms in App" in html


def test_help_terms_of_use_route_renders_freight_services_content() -> None:
    """Serve the full Freight Services terms of use page for end users.

    Args:
        None.

    Returns:
        None. Assertions verify legal headings and contact details render.

    External dependencies:
        * Calls ``help.terms_of_use`` through
          :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()

    response = client.get("/help/terms-of-use")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Freight Services Terms and Conditions" in html
    assert "Class Action Waiver" in html
    assert "Liability Disclaimer" in html
    assert "humanresources@freightservices.net" in html


def test_help_index_hides_employee_resources_for_customers() -> None:
    """Keep employee-only links out of the public help landing page.

    Args:
        None.

    Returns:
        None. Assertions verify internal resources are omitted for customers.

    External dependencies:
        * Calls ``help.help_index`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()
    client.get("/auth/login-as/customer")

    response = client.get("/help/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Internal technical notes:" not in html
    assert "Keep customer-facing language focused on speed" not in html


def test_help_index_shows_employee_resources_for_internal_users() -> None:
    """Keep help center output consistent for employee users.

    Args:
        None.

    Returns:
        None. Assertions verify section content visible to employees.

    External dependencies:
        * Calls ``help.help_index`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()
    client.get("/auth/login-as/employee")

    response = client.get("/help/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Shipment Calculation Guide" in html
    assert "Read Full Terms in App" in html


def test_help_index_treats_company_email_as_internal_even_without_employee_role() -> (
    None
):
    """Render standard help content for company-domain customer accounts.

    Args:
        None.

    Returns:
        None. Assertions verify the public help sections still render.

    External dependencies:
        * Calls ``help.help_index`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()
    client.get("/auth/login-as/domain_customer")

    response = client.get("/help/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Shipment Calculation Guide" in html
    assert "Internal technical notes:" not in html


def test_help_admin_requires_employee_authentication() -> None:
    """Protect internal admin help documentation behind employee login.

    Args:
        None.

    Returns:
        None. Assertions verify anonymous users are redirected to login.

    External dependencies:
        * Calls ``help.admin`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()

    response = client.get("/help/admin")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_help_emailing_requires_employee_authentication() -> None:
    """Protect internal email workflow docs behind employee login.

    Args:
        None.

    Returns:
        None. Assertions verify anonymous users are redirected to login.

    External dependencies:
        * Calls ``help.emailing_guide`` through :meth:`flask.testing.FlaskClient.get`.
    """

    app = _build_help_test_app()
    client = app.test_client()

    response = client.get("/help/emailing")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
