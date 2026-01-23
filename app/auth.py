# app/auth.py
"""Define the authentication blueprint and its user-facing routes.

The blueprint wraps the authentication lifecycle for the quote tool:

- ``/login`` authenticates existing users and starts a session via
  :func:`flask_login.login_user`.
- ``/register`` provisions accounts after validating contact details and a
  simple human-verification challenge.
- ``/logout`` ends an authenticated session with
  :func:`flask_login.logout_user`.
- ``/reset`` and ``/reset/<token>`` implement a self-service password reset
  flow backed by helpers in :mod:`services.auth_utils`. Because outbound email
  is unavailable, the reset request view now surfaces tokens directly for the
  signed-in user so the link can be copied in-app.
"""

import secrets
from datetime import datetime
from typing import Dict, Optional, Union, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from flask_limiter.util import get_remote_address
from authlib.integrations.base_client.errors import OAuthError

from .models import db, User, PasswordResetToken
from app.services.auth_utils import (
    EMPLOYEE_EMAIL_DOMAIN,
    authenticate,
    is_valid_password,
    is_valid_email,
    is_valid_phone,
    create_reset_token,
    hash_reset_token,
    reset_password_with_token,
    provision_employee_from_oidc,
)
from app import limiter
from app.services.oidc_client import get_oidc_client, is_oidc_configured

auth_bp = Blueprint("auth", __name__, template_folder="templates")


def _remote_limit_scope(identifier: Optional[str]) -> str:
    """Return a composite key combining the caller's IP and identifier.

    Args:
        identifier: Optional string to append to the caller's IP address.

    Returns:
        A string scoped for :mod:`flask_limiter` that couples
        :func:`flask_limiter.util.get_remote_address` with the provided
        identifier. The helper ensures repeated attempts from the same network
        and account are rate limited together.
    """

    base_ip = request.remote_addr or get_remote_address()
    if identifier:
        candidate = identifier.strip().lower()
        if candidate:
            return f"{base_ip}:{candidate}"
    return base_ip


def _login_rate_limit_value() -> str:
    """Return the configured rate limit string for :func:`login` requests.

    Pulls :data:`flask.current_app.config['AUTH_LOGIN_RATE_LIMIT']` so
    deployments can tune the protection without code changes. Defaults to
    ``"5 per minute"`` when no configuration is provided.
    """

    value = current_app.config.get("AUTH_LOGIN_RATE_LIMIT", "5 per minute")
    return str(value or "5 per minute")


def _login_rate_limit_key() -> str:
    """Scope login attempts by remote IP and submitted email address.

    Uses :data:`flask.request.form` to read the submitted ``email`` field and
    delegates formatting to :func:`_remote_limit_scope` so IP and account based
    throttles are enforced together.
    """

    return _remote_limit_scope(request.form.get("email"))


def _reset_rate_limit_value() -> str:
    """Return the rate limit string applied to :func:`reset_request`.

    Mirrors :func:`_login_rate_limit_value` but uses the
    ``AUTH_RESET_RATE_LIMIT`` configuration key with a ``"5 per minute"``
    fallback. Keeping the helper separate clarifies which setting governs each
    endpoint for operators.
    """

    value = current_app.config.get("AUTH_RESET_RATE_LIMIT", "5 per minute")
    return str(value or "5 per minute")


def _reset_rate_limit_key() -> str:
    """Scope password reset requests by caller identity.

    When an authenticated user triggers the in-app reset flow we derive the
    limiter key from :data:`flask_login.current_user`, falling back to the
    user's email address so repeated requests within a session share the same
    bucket. Unauthenticated callers rely on the submitted ``email`` field which
    keeps the legacy behaviour intact for administrators who may POST on behalf
    of another user.
    """

    if current_user.is_authenticated:
        identifier = (current_user.get_id() or "").strip() or (
            getattr(current_user, "email", "")
        )
        return _remote_limit_scope(identifier)
    return _remote_limit_scope(request.form.get("email"))


def _register_rate_limit_value() -> str:
    """Return the rate limit string applied to :func:`register` requests.

    Reads :data:`flask.current_app.config['AUTH_REGISTER_RATE_LIMIT']` so
    deployments can tailor registration throttling without code changes. The
    helper defaults to ``"5 per minute"`` to mirror the login and reset
    behaviour when the configuration key is absent or empty.
    """

    value = current_app.config.get("AUTH_REGISTER_RATE_LIMIT", "5 per minute")
    return str(value or "5 per minute")


def _register_rate_limit_key() -> str:
    """Scope registration attempts by remote IP address and email address.

    Leverages :func:`_remote_limit_scope` to combine
    :func:`flask_limiter.util.get_remote_address` with the submitted
    ``email`` field from :data:`flask.request.form`. Grouping on both values
    ensures repeated submissions for the same account from a single network are
    throttled together.
    """

    return _remote_limit_scope(request.form.get("email"))


def _issue_registration_challenge() -> str:
    """Return a math prompt used to verify a human visitor.

    Generates two random numbers using :mod:`secrets` and stores the
    expected sum in :data:`flask.session['registration_challenge_answer']` so
    :func:`register` can validate the user's response on submission.

    Returns:
        A human-readable question such as ``"What is 3 + 5?"`` for display
        in the registration form template.
    """

    first_term = secrets.randbelow(8) + 2  # Range 2-9 keeps math accessible.
    second_term = secrets.randbelow(8) + 2
    session["registration_challenge_answer"] = str(first_term + second_term)
    return f"What is {first_term} + {second_term}?"


PASSWORD_REQUIREMENTS_HELP = (
    "Use at least 14 characters with upper- and lower-case letters, a number, and a "
    "symbol, or supply a 24+ character passphrase."
)


def _account_settings_form_state(user: User) -> Dict[str, str]:
    """Return sanitized defaults for the account settings form.

    Args:
        user: Authenticated :class:`~app.models.User` whose profile is being edited.

    Returns:
        Dictionary keyed by the HTML form fields rendered in
        ``templates/settings.html``. The helper centralizes how we derive default
        values from the user model so both GET and POST handlers stay consistent.
    """

    return {
        "first_name": (getattr(user, "first_name", "") or "").strip(),
        "last_name": (getattr(user, "last_name", "") or "").strip(),
        "email": (getattr(user, "email", "") or "").strip(),
        "phone": (getattr(user, "phone", "") or "").strip(),
        "company_name": (getattr(user, "company_name", "") or "").strip(),
        "company_phone": (getattr(user, "company_phone", "") or "").strip(),
    }


def _configured_employee_domain() -> str:
    """Return the email domain used to flag Freight Services employees."""

    configured = (current_app.config.get("OIDC_ALLOWED_DOMAIN") or "").strip().lower()
    if configured:
        return configured.lstrip("@") or configured
    return EMPLOYEE_EMAIL_DOMAIN.lstrip("@")


def _employee_email_suffix() -> str:
    """Return the ``@domain`` suffix representing Freight Services staff."""

    return f"@{_configured_employee_domain()}"


def _clear_oidc_session() -> None:
    """Remove transient OIDC values stored in :data:`flask.session`."""

    for key in ("oidc_state", "oidc_nonce", "oidc_expected_email"):
        session.pop(key, None)


def _build_oidc_logout_url(logout_endpoint: str) -> str:
    """Return the IdP logout URL with a post-logout redirect back to Flask."""

    parsed = urlparse(logout_endpoint)
    query = dict(parse_qsl(parsed.query))
    query["post_logout_redirect_uri"] = url_for("auth.login", _external=True)
    encoded = urlencode(query)
    return urlunparse(parsed._replace(query=encoded))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    _login_rate_limit_value, key_func=_login_rate_limit_key, methods=["POST"]
)
def login() -> Union[str, Response]:
    """Authenticate a user and start a session.

    Required form fields:
        - email
        - password (customer accounts only)

    Related helpers:
        - is_valid_email
        - login_user

    Returns:
        Renders ``login.html`` on GET or failed login.
        Redirects to ``quotes.new_quote`` on success.
    """
    oidc_available = is_oidc_configured()
    employee_suffix = _employee_email_suffix()
    employee_domain = _configured_employee_domain()

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not is_valid_email(email):
            flash("Invalid email address.", "warning")
            return redirect(url_for("auth.login"))

        if oidc_available and email.endswith(employee_suffix):
            session["oidc_expected_email"] = email
            return redirect(url_for("auth.login_oidc"))
        password = request.form.get("password", "")
        user, error = authenticate(email, password)
        if user:
            login_user(user)
            current_app.logger.info(
                "login successful for %s", getattr(user, "email", "<missing>")
            )
            return redirect(url_for("quotes.new_quote"))
        flash("Invalid credentials", "danger")

    return render_template(
        "login.html",
        oidc_enabled=oidc_available,
        employee_domain=f"@{employee_domain}",
        oidc_login_url=url_for("auth.login_oidc", employee_domain=employee_domain),
    )


@auth_bp.route("/login/oidc")
def login_oidc() -> Response:
    """Start the OpenID Connect login flow for Freight Services employees."""

    if not is_oidc_configured():
        flash("Single sign-on is not available.", "warning")
        return redirect(url_for("auth.login"))

    client = get_oidc_client()
    if client is None:
        flash("Single sign-on is temporarily unavailable.", "danger")
        return redirect(url_for("auth.login"))

    nonce = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    session["oidc_nonce"] = nonce
    session["oidc_state"] = state

    redirect_uri = current_app.config.get("OIDC_REDIRECT_URI") or url_for(
        "auth.login_oidc_callback", _external=True
    )

    try:
        return client.authorize_redirect(
            redirect_uri,
            state=state,
            nonce=nonce,
        )
    except OAuthError as exc:
        current_app.logger.exception("OIDC authorization redirect failed: %s", exc)
        _clear_oidc_session()
        flash("Failed to contact the identity provider. Please try again.", "danger")
        return redirect(url_for("auth.login"))
    except Exception as exc:  # pragma: no cover - defensive guard
        current_app.logger.exception("Unexpected OIDC error: %s", exc)
        _clear_oidc_session()
        flash("Single sign-on is unavailable right now.", "danger")
        return redirect(url_for("auth.login"))


@auth_bp.route("/login/oidc/callback")
def login_oidc_callback() -> Response:
    """Handle the OpenID Connect authorization code response."""

    if not is_oidc_configured():
        flash("Single sign-on is not available.", "warning")
        return redirect(url_for("auth.login"))

    client = get_oidc_client()
    if client is None:
        flash("Single sign-on is temporarily unavailable.", "danger")
        return redirect(url_for("auth.login"))

    expected_state = session.get("oidc_state")
    expected_email = session.get("oidc_expected_email")
    nonce = session.get("oidc_nonce")
    received_state = request.args.get("state", "")
    if not expected_state or expected_state != received_state:
        _clear_oidc_session()
        flash("Login session expired. Please start again.", "warning")
        return redirect(url_for("auth.login"))

    try:
        token = client.authorize_access_token()
    except OAuthError as exc:
        current_app.logger.exception("OIDC token exchange failed: %s", exc)
        _clear_oidc_session()
        flash("Could not complete single sign-on. Please try again.", "danger")
        return redirect(url_for("auth.login"))
    except Exception as exc:  # pragma: no cover - defensive guard
        current_app.logger.exception("Unexpected OIDC token error: %s", exc)
        _clear_oidc_session()
        flash("Single sign-on is unavailable right now.", "danger")
        return redirect(url_for("auth.login"))

    try:
        id_token = client.parse_id_token(token, nonce=nonce)
    except (OAuthError, ValueError) as exc:
        current_app.logger.exception("OIDC ID token validation failed: %s", exc)
        _clear_oidc_session()
        flash("Identity provider response was invalid.", "danger")
        return redirect(url_for("auth.login"))
    except Exception as exc:  # pragma: no cover - defensive guard
        current_app.logger.exception("Unexpected OIDC validation error: %s", exc)
        _clear_oidc_session()
        flash("Single sign-on is unavailable right now.", "danger")
        return redirect(url_for("auth.login"))

    _clear_oidc_session()

    email = str(id_token.get("email", "")).strip().lower()
    if not email:
        flash("The identity provider did not return an email address.", "danger")
        return redirect(url_for("auth.login"))

    if expected_email and email != expected_email:
        flash("Email mismatch detected during login. Please try again.", "warning")
        return redirect(url_for("auth.login"))

    allowed_suffix = _employee_email_suffix()
    if not email.endswith(allowed_suffix):
        flash("Single sign-on is restricted to Freight Services employees.", "danger")
        return redirect(url_for("auth.login"))

    raw_audience = current_app.config.get("OIDC_AUDIENCE", ())
    if isinstance(raw_audience, str):
        configured_audience = {raw_audience}
    else:
        configured_audience = {str(value) for value in raw_audience or ()}
    if configured_audience:
        token_audience = id_token.get("aud")
        if isinstance(token_audience, str):
            audience_values = {token_audience}
        else:
            audience_values = {str(value) for value in token_audience or []}
        if not set(configured_audience).intersection(audience_values):
            flash(
                "The identity provider response is not intended for this application.",
                "danger",
            )
            return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=email).first()
    if user is None:
        given_name = str(id_token.get("given_name", "")).strip() or None
        family_name = str(id_token.get("family_name", "")).strip() or None
        full_name = str(id_token.get("name", "")).strip() or None
        try:
            user = provision_employee_from_oidc(
                email=email,
                given_name=given_name,
                family_name=family_name,
                full_name=full_name,
            )
        except ValueError as exc:
            current_app.logger.exception("OIDC provisioning failed: %s", exc)
            flash("Could not create an employee account automatically.", "danger")
            return redirect(url_for("auth.login"))
        flash(
            "Account created. An administrator will finish enabling employee access.",
            "info",
        )
    else:
        updated = False
        given_name = str(id_token.get("given_name", "")).strip()
        family_name = str(id_token.get("family_name", "")).strip()
        full_name = str(id_token.get("name", "")).strip()
        if given_name and given_name != (user.first_name or ""):
            user.first_name = given_name
            updated = True
        if family_name and family_name != (user.last_name or ""):
            user.last_name = family_name
            updated = True
        if full_name and full_name != (user.name or ""):
            user.name = full_name
            updated = True
        if user.role not in {"employee", "super_admin"}:
            user.role = "employee"
            updated = True
        if updated:
            db.session.add(user)
            db.session.commit()

    if not getattr(user, "is_active", True):
        flash(
            "This account is disabled. Contact an administrator for assistance.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    login_user(user)
    return redirect(url_for("quotes.new_quote"))


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit(
    _register_rate_limit_value,
    key_func=_register_rate_limit_key,
    methods=["POST"],
)
def register() -> Union[str, Response]:
    """Register a new user account.

    Required form fields:
        - first_name
        - last_name
        - email
        - phone
        - company_name
        - company_phone
        - password
        - confirm_password
        - human_verification

    Related helpers:
        - is_valid_email
        - is_valid_password
        - is_valid_phone
        - _issue_registration_challenge

    Special cases:
        - Addresses ending in ``@freightservices.net`` are flagged as employee
          registrations. These accounts are created with ``role="employee"``
          and ``employee_approved=False`` until an administrator reviews them.
          The function also logs an info-level message for administrators so
          they can approve the new employee.

    Returns:
        Renders ``register.html`` on GET or validation failure.
        Redirects to ``auth.login`` on success.
    """
    if request.method == "POST":
        first_name = (request.form.get("first_name") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        company_name = (request.form.get("company_name") or "").strip()
        company_phone = (request.form.get("company_phone") or "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        human_verification = (request.form.get("human_verification") or "").strip()

        missing_fields = [
            label
            for label, value in {
                "first name": first_name,
                "last name": last_name,
                "email": email,
                "phone number": phone,
                "company name": company_name,
                "company phone number": company_phone,
                "password": password,
                "password confirmation": confirm_password,
                "human verification answer": human_verification,
            }.items()
            if not value
        ]
        if missing_fields:
            field_list = ", ".join(missing_fields)
            flash(f"Please provide: {field_list}.", "warning")
            return redirect(url_for("auth.register"))

        expected_answer = session.get("registration_challenge_answer")
        if not expected_answer:
            flash("Human verification expired. Please try again.", "warning")
            return redirect(url_for("auth.register"))

        if human_verification != expected_answer:
            flash("Incorrect human verification answer.", "warning")
            return redirect(url_for("auth.register"))

        if not is_valid_phone(phone):
            flash("Enter a valid phone number.", "warning")
            return redirect(url_for("auth.register"))

        if not is_valid_phone(company_phone):
            flash("Enter a valid company phone number.", "warning")
            return redirect(url_for("auth.register"))

        if not is_valid_email(email):
            flash("Invalid email address.", "warning")
            return redirect(url_for("auth.register"))

        freight_employee_signup = email.endswith("@freightservices.net")

        if password != confirm_password:
            flash("Passwords do not match.", "warning")
            return redirect(url_for("auth.register"))

        if not is_valid_password(password):
            flash("Password does not meet complexity requirements.", "warning")
            return redirect(url_for("auth.register"))

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "warning")
            return redirect(url_for("auth.register"))

        full_name = f"{first_name} {last_name}".strip()
        user = User(
            email=email,
            name=full_name,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            company_name=company_name,
            company_phone=company_phone,
        )
        if freight_employee_signup:
            user.role = "employee"
            user.employee_approved = False
            current_app.logger.info(
                "Pending employee registration for %s requires approval.",
                email,
            )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session.pop("registration_challenge_answer", None)
        flash("Registered. Please log in.", "success")
        return redirect(url_for("auth.login"))
    challenge_prompt = _issue_registration_challenge()
    return render_template("register.html", challenge_prompt=challenge_prompt)


@auth_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings() -> Union[str, Response]:
    """Allow authenticated users to maintain their own account details.

    The view supports updating contact information collected at registration and
    optionally changing the password when the caller provides their current
    credentials. Validation mirrors the registration form using helpers from
    :mod:`services.auth_utils`, including
    :func:`services.auth_utils.is_valid_email`,
    :func:`services.auth_utils.is_valid_phone`, and
    :func:`services.auth_utils.is_valid_password`.

    Returns:
        Renders ``settings.html`` with any validation feedback when invoked via
        GET or after an invalid submission. Redirects back to ``auth.settings``
        after persisting updates so refreshes do not resubmit form data.
    """

    user = cast(User, current_user)
    form_state = _account_settings_form_state(user)
    if request.method == "POST":
        first_name = (request.form.get("first_name") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        company_name = (request.form.get("company_name") or "").strip()
        company_phone = (request.form.get("company_phone") or "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        form_state.update(
            {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "company_name": company_name,
                "company_phone": company_phone,
            }
        )

        errors = []
        if not first_name:
            errors.append("First name is required.")
        if not last_name:
            errors.append("Last name is required.")
        if not phone:
            errors.append("Phone number is required.")
        elif not is_valid_phone(phone):
            errors.append("Enter a valid phone number.")
        if not company_name:
            errors.append("Company name is required.")
        if not company_phone:
            errors.append("Company phone number is required.")
        elif not is_valid_phone(company_phone):
            errors.append("Enter a valid company phone number.")
        if not email or not is_valid_email(email):
            errors.append("Enter a valid email address.")
        else:
            existing = User.query.filter(
                User.email == email,
                User.id != user.id,
            ).first()
            if existing:
                errors.append("Another account already uses that email address.")

        password_updated = False
        attempted_password_change = (
            current_password.strip() or new_password.strip() or confirm_password.strip()
        )
        if attempted_password_change:
            if not current_password:
                errors.append("Enter your current password to set a new one.")
            elif not user.check_password(current_password):
                errors.append("Current password is incorrect.")
            if not new_password:
                errors.append("New password is required.")
            if new_password != confirm_password:
                errors.append("New password and confirmation do not match.")
            if new_password and not is_valid_password(new_password):
                errors.append("New password does not meet complexity requirements.")
            if not errors and new_password:
                user.set_password(new_password)
                password_updated = True

        if errors:
            for message in errors:
                flash(message, "warning")
            return render_template(
                "settings.html",
                form_state=form_state,
                password_hint=PASSWORD_REQUIREMENTS_HELP,
            )

        user.first_name = first_name
        user.last_name = last_name
        user.phone = phone
        user.company_name = company_name
        user.company_phone = company_phone
        user.email = email
        user.name = f"{first_name} {last_name}".strip()
        db.session.add(user)
        db.session.commit()

        flash("Account settings updated.", "success")
        if password_updated:
            flash("Your password has been updated.", "success")
        return redirect(url_for("auth.settings"))

    return render_template(
        "settings.html",
        form_state=form_state,
        password_hint=PASSWORD_REQUIREMENTS_HELP,
    )


@auth_bp.route("/logout")
@login_required
def logout() -> Response:
    """End the current user session.

    Required form fields: none

    Related helpers:
        - logout_user

    Returns:
        Redirects to ``auth.login`` after logout. When OIDC is configured with
        an end-session endpoint the browser is routed through the identity
        provider so upstream sessions are cleared too.
    """
    logout_user()
    _clear_oidc_session()
    logout_endpoint = current_app.config.get("OIDC_END_SESSION_ENDPOINT")
    if logout_endpoint and is_oidc_configured():
        return redirect(_build_oidc_logout_url(logout_endpoint))
    return redirect(url_for("auth.login"))


@auth_bp.route("/reset", methods=["GET", "POST"])
@limiter.limit(
    _reset_rate_limit_value, key_func=_reset_rate_limit_key, methods=["POST"]
)
def reset_request() -> Union[str, Response]:
    """Start the password reset workflow for the signed-in user.

    Only authenticated users can generate a self-service reset link because the
    tool cannot currently deliver email. Team members who are logged out should
    contact an administrator so their identity can be verified manually.

    Related helpers:
        - is_valid_email
        - create_reset_token

    Returns:
        Renders ``reset_request.html`` on GET, invalid submission, or after a
        successful token creation so the caller can copy the link directly.
    """

    if not current_user.is_authenticated:
        # Signed-out visitors receive guidance to contact an administrator. We
        # still render the template without processing form data so the view can
        # display consistent messaging.
        return render_template("reset_request.html", requires_auth=True)

    email = (getattr(current_user, "email", "") or "").strip().lower()
    context: Dict[str, str] = {"account_email": email}
    if request.method == "POST":
        if not is_valid_email(email):
            flash(
                "We could not verify the email on your account. Please contact an "
                "administrator for assistance.",
                "warning",
            )
            return render_template("reset_request.html", **context)
        token, error = create_reset_token(email)
        if error == "Reset already requested recently. Please wait.":
            flash(error, "warning")
            return redirect(url_for("auth.reset_request"))
        if token:
            reset_url = url_for("auth.reset_with_token", token=token, _external=True)
            context["reset_url"] = reset_url
            context["reset_token"] = token
            flash(
                "Copy the secure link below to continue resetting your password.",
                "info",
            )
        else:
            flash(
                "If your account is eligible, a reset link will appear here once "
                "generated.",
                "info",
            )
        return render_template("reset_request.html", **context)
    return render_template("reset_request.html", **context)


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_with_token(token: str) -> Union[str, Response]:
    """Reset a user's password using a token.

    Required form fields:
        - new_password
        - confirm_password

    Related helpers:
        - reset_password_with_token

    Returns:
        Renders ``reset_password.html`` when token is valid and request is GET.
        Redirects to ``auth.login`` on success or ``auth.reset_request`` on failure.
    """
    if request.method == "POST":
        password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if password != confirm:
            flash("Passwords do not match.", "warning")
            return redirect(url_for("auth.reset_with_token", token=token))
        error = reset_password_with_token(token, password)
        if error:
            flash(error, "danger")
            return redirect(url_for("auth.reset_request"))
        flash("Password has been reset. Please log in.", "success")
        return redirect(url_for("auth.login"))
    hashed_token = hash_reset_token(token)
    reset = PasswordResetToken.query.filter_by(token=hashed_token, used=False).first()
    if not reset or reset.expires_at < datetime.utcnow():
        flash("Invalid or expired token.", "danger")
        return redirect(url_for("auth.reset_request"))
    return render_template("reset_password.html")
