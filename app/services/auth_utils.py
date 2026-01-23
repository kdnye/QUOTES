"""Authentication utilities."""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from flask import current_app

from app.models import db, User, PasswordResetToken
from email_validator import EmailNotValidError, validate_email
from limits import parse as parse_rate_limit
from limits.limits import RateLimitItem


EMPLOYEE_EMAIL_DOMAIN = "@freightservices.net"
DEFAULT_RESET_TOKEN_RATE_LIMIT = "1 per 15 minutes"


def hash_reset_token(token: str) -> str:
    """Return a deterministic hash for password reset tokens.

    Args:
        token: Raw token string that will be sent to the user via email.

    Returns:
        Hex-encoded digest produced by :func:`hashlib.sha256`. The digest is
        stored in the database so leaked rows cannot be used to reset
        passwords.
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_valid_password(password: str) -> bool:
    """Validate password complexity or allow long passphrases."""
    if len(password) >= 24:
        return True
    return (
        len(password) >= 14
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and re.search(r"[0-9]", password)
        and re.search(r"[^a-zA-Z0-9]", password)
    )


def is_valid_email(address: str) -> bool:
    """Return True if the email address is syntactically valid."""
    try:
        validate_email(address, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def authenticate(email: str, password: str) -> Tuple[Optional[User], Optional[str]]:
    """Return the :class:`app.models.User` matching the provided credentials.

    The helper normalizes the supplied email to lower case and compares it
    against ``User.email`` using a case-insensitive filter. This ensures
    accounts seeded with upper-case characters (for example via
    ``ADMIN_EMAIL``) remain reachable even when callers submit lower-case
    credentials. Password verification is delegated to
    :meth:`app.models.User.check_password`, and inactive accounts are rejected
    so callers receive a consistent error message.

    Args:
        email: Email address submitted by the user.
        password: Plain-text password to validate with the stored hash.

    Returns:
        Tuple of ``(user, error_message)`` where ``user`` is the matching
        :class:`~app.models.User` on success (``None`` on failure) and
        ``error_message`` describes the reason when authentication fails.
    """

    normalized_email = email.strip().lower()
    if not is_valid_email(normalized_email):
        return None, "Invalid email address"

    user = (
        User.query.filter(db.func.lower(User.email) == normalized_email)
        .order_by(User.id.asc())
        .first()
    )
    if not user or not user.check_password(password):
        return None, "Invalid credentials"
    if not getattr(user, "is_active", True):
        return None, "Account inactive"
    return user, None


def is_valid_phone(phone: str) -> bool:
    """Return ``True`` when ``phone`` resembles a dialable number."""

    cleaned = re.sub(r"[^0-9]", "", phone)
    if len(cleaned) < 7 or len(cleaned) > 15:
        return False
    return bool(re.fullmatch(r"[0-9+().\-\s]+", phone))


def register_user(
    data: dict, auto_approve: bool = False
) -> Tuple[Optional[User], Optional[str]]:
    """Register a new user account.

    The helper validates contact details using
    :func:`services.auth_utils.is_valid_phone`, enforces password requirements
    via :func:`services.auth_utils.is_valid_password`, and normalizes email
    addresses with :func:`services.auth_utils.is_valid_email`. Addresses ending
    in ``@freightservices.net`` are automatically treated as employee accounts;
    their ``role`` is forced to ``"employee"`` and they remain unapproved until
    an administrator reviews the request. A log message is emitted so admins can
    spot the pending employee.

    Args:
        data: Dictionary containing registration form fields including optional
            ``role`` and ``employee_approved`` overrides.
        auto_approve: When ``True`` the account is activated immediately.

    Returns:
        Tuple whose first element is the created :class:`app.models.User` on
        success (``None`` when validation fails) and whose second element is an
        error message string when validation fails (``None`` on success).

    Required ``data`` keys:
        ``first_name``, ``last_name``, ``phone``, ``company_name``,
        ``company_phone``, ``email``, ``password``.
    """

    allowed_roles = {"customer", "employee", "super_admin"}

    first_name = (data.get("first_name") or "").strip()
    if not first_name:
        return None, "First name is required."

    last_name = (data.get("last_name") or "").strip()
    if not last_name:
        return None, "Last name is required."

    phone = (data.get("phone") or "").strip()
    if not phone:
        return None, "Phone number is required."
    if not is_valid_phone(phone):
        return None, "Enter a valid phone number."

    company_name = (data.get("company_name") or "").strip()
    if not company_name:
        return None, "Company name is required."

    company_phone = (data.get("company_phone") or "").strip()
    if not company_phone:
        return None, "Company phone number is required."
    if not is_valid_phone(company_phone):
        return None, "Enter a valid company phone number."

    email = (data.get("email") or "").strip().lower()
    if not is_valid_email(email):
        return None, "Invalid email address."

    freight_employee_signup = email.endswith(EMPLOYEE_EMAIL_DOMAIN)

    password = data.get("password") or ""
    if not is_valid_password(password):
        return None, "Password does not meet complexity requirements."

    if User.query.filter_by(email=email).first():
        return None, "Email already registered."

    raw_role = data.get("role")
    if raw_role is None:
        role_value = "customer"
    elif isinstance(raw_role, str):
        role_value = raw_role.strip().lower() or "customer"
    else:
        return None, "Invalid role."
    if role_value not in allowed_roles:
        return None, "Invalid role."

    raw_employee_flag = data.get("employee_approved", False)
    if isinstance(raw_employee_flag, str):
        employee_approved = raw_employee_flag.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
    else:
        employee_approved = bool(raw_employee_flag)

    if freight_employee_signup:
        role_value = "employee"
        employee_approved = False
        message = "Pending employee registration for %s requires approval."
        try:
            current_app.logger.info(message, email)
        except RuntimeError:
            logging.getLogger("quote_tool.admin").info(message, email)
    elif role_value == "super_admin":
        employee_approved = True
    elif role_value != "employee":
        employee_approved = False

    full_name = f"{first_name} {last_name}".strip()
    new_user = User(
        name=full_name,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        company_name=company_name,
        company_phone=company_phone,
        email=email,
        role=role_value,
        employee_approved=employee_approved,
        is_active=auto_approve,
    )
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    return new_user, None


def provision_employee_from_oidc(
    *,
    email: str,
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    full_name: Optional[str] = None,
) -> User:
    """Create a Freight Services employee for IdP-managed authentication.

    Args:
        email: Employee email address returned by the identity provider.
        given_name: Optional first name claim extracted from the ID token.
        family_name: Optional last name claim extracted from the ID token.
        full_name: Optional display name claim extracted from the ID token.

    Returns:
        Newly created :class:`app.models.User` with ``role="employee"`` and
        ``employee_approved=False`` so administrators can grant elevated access.

    External Dependencies:
        * Persists data using :data:`app.models.db.session`.
        * Hashes a randomly generated password via
          :meth:`app.models.User.set_password` to satisfy the non-null
          constraint on ``users.password_hash``.
    """

    normalized_email = email.strip().lower()
    if not normalized_email.endswith(EMPLOYEE_EMAIL_DOMAIN):
        raise ValueError(
            "Only Freight Services employee emails can use SSO provisioning."
        )

    if User.query.filter_by(email=normalized_email).first():
        raise ValueError("Account already exists for this email address.")

    first_name = (given_name or "").strip()
    last_name = (family_name or "").strip()
    display_name = (full_name or "").strip()
    if not display_name:
        display_name = f"{first_name} {last_name}".strip()
    if not display_name:
        display_name = normalized_email.split("@", 1)[0]

    user = User(
        email=normalized_email,
        first_name=first_name or None,
        last_name=last_name or None,
        name=display_name,
        role="employee",
        employee_approved=False,
        is_active=True,
    )
    user.set_password(secrets.token_urlsafe(32))
    db.session.add(user)
    db.session.commit()
    return user


def list_users():
    return User.query.all()


def _resolve_reset_token_limit() -> RateLimitItem:
    """Return the rate limit configuration for password reset tokens.

    The helper reads :data:`flask.current_app.config['AUTH_RESET_TOKEN_RATE_LIMIT']`
    so deployments can adjust throttling without modifying code. When the
    configuration is missing or invalid the default of "1 per 15 minutes" is
    parsed instead. Any parsing errors are logged using the application logger
    (if available) or the module-level logger otherwise. The return value is a
    :class:`limits.limits.RateLimitItem` ready for consumption by
    :mod:`flask_limiter` utilities.

    Returns:
        RateLimitItem: Parsed rate limit used to gate token issuance.
    """

    try:
        configured_value = current_app.config.get(
            "AUTH_RESET_TOKEN_RATE_LIMIT", DEFAULT_RESET_TOKEN_RATE_LIMIT
        )
    except RuntimeError:
        configured_value = DEFAULT_RESET_TOKEN_RATE_LIMIT
    limit_text = str(configured_value or DEFAULT_RESET_TOKEN_RATE_LIMIT)
    try:
        return parse_rate_limit(limit_text)
    except ValueError:
        logger = getattr(current_app, "logger", logging.getLogger(__name__))
        logger.warning(
            "Invalid AUTH_RESET_TOKEN_RATE_LIMIT %r; using %s",
            limit_text,
            DEFAULT_RESET_TOKEN_RATE_LIMIT,
        )
        return parse_rate_limit(DEFAULT_RESET_TOKEN_RATE_LIMIT)


def _limiter_allows_reset_token(user_id: int, limit: RateLimitItem) -> bool:
    """Return ``True`` when a password reset token may be issued.

    The helper first attempts to consume the configured rate limit using the
    global :data:`app.limiter` extension. This lets
    :func:`services.auth_utils.create_reset_token` share counters with the rest
    of the application and respect remote storage backends configured via
    ``RATELIMIT_STORAGE_URI``. If the limiter is unavailable or raises an
    exception, the function falls back to a database query that mirrors the
    previous "look back N minutes" behaviour, where ``N`` is derived from the
    parsed rate limit.

    Args:
        user_id: Identifier of the :class:`app.models.User` requesting a token.
        limit: Parsed :class:`limits.limits.RateLimitItem` describing the
            allowed frequency.

    Returns:
        ``True`` when token creation should proceed, ``False`` when throttled.
    """

    limiter_strategy = None
    try:
        from app import limiter  # Local import avoids circular dependencies.

        limiter_strategy = limiter.limiter
    except Exception:  # pragma: no cover - defensive guard
        limiter_strategy = None

    if limiter_strategy is not None:
        identifier = f"password-reset-token:{user_id}"
        try:
            if not limiter_strategy.hit(limit, identifier):
                return False
            return True
        except Exception:  # pragma: no cover - falls back to DB check
            logging.getLogger("quote_tool.auth").exception(
                "Falling back to database window for reset token limiter"
            )

    window_seconds = limit.get_expiry()
    if window_seconds:
        cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
        recent = PasswordResetToken.query.filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.created_at >= cutoff,
            PasswordResetToken.used.is_(False),
        ).first()
        if recent:
            return False
    return True


def create_reset_token(email: str) -> Tuple[Optional[str], Optional[str]]:
    """Create a one-use reset token for the user with given email.

    The helper validates the address with :func:`is_valid_email`, looks up the
    associated :class:`app.models.User`, and enforces the configurable
    ``AUTH_RESET_TOKEN_RATE_LIMIT`` using :mod:`flask_limiter`. Deployments may
    tune the throttling string (for example, ``"2 per hour"``) without touching
    the code. When the limiter is unavailable the function gracefully reverts to
    a database look-back window derived from the same configuration value. The
    generated token is stored as a SHA-256 digest via
    :func:`services.auth_utils.hash_reset_token` so compromised database rows do
    not expose usable reset links.
    """
    if not is_valid_email(email):
        return None, "Invalid email address."
    user = User.query.filter_by(email=email).first()
    if not user:
        return None, "No user found with that email."
    limit = _resolve_reset_token_limit()
    if not _limiter_allows_reset_token(user.id, limit):
        return None, "Reset already requested recently. Please wait."
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    reset_token = PasswordResetToken(
        user_id=user.id, token=hash_reset_token(token), expires_at=expires_at
    )
    db.session.add(reset_token)
    db.session.commit()
    return token, None


def reset_password_with_token(token: str, new_password: str) -> Optional[str]:
    """Reset user password using a valid token.

    Args:
        token: Raw token supplied by the user from their reset email.
        new_password: Proposed replacement password.

    Returns:
        ``None`` on success or an error message string when validation fails.
        The lookup hashes ``token`` with
        :func:`services.auth_utils.hash_reset_token` before querying the
        database so stolen rows remain unusable.
    """
    if not is_valid_password(new_password):
        return "Password does not meet complexity requirements."
    reset = PasswordResetToken.query.filter_by(
        token=hash_reset_token(token), used=False
    ).first()
    if not reset or reset.expires_at < datetime.utcnow():
        return "Invalid or expired token."
    user = db.session.get(User, reset.user_id)
    if not user:
        return "Invalid token."
    user.set_password(new_password)
    reset.used = True
    db.session.commit()
    return None
