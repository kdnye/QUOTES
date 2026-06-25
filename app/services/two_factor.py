"""Email-delivered two-factor authentication (2FA) for password logins.

This service implements the second factor of the login flow: after a user
proves they know their password, a short numeric code is generated, hashed,
stored, and emailed to the address on file. The user must read that code from
their inbox and submit it to finish logging in.

Why email codes (instead of an authenticator app):

    The codes are sent to the user's verified email address, so the ability to
    log in is tied to ongoing control of that mailbox. When a partner company
    deactivates a former employee's email account, that person can no longer
    receive the code and is therefore locked out of this application
    automatically — no manual offboarding step required on our side.

Design notes:

    * Codes are stored only as SHA-256 digests (see :func:`hash_login_code`),
      mirroring the password-reset token design in
      :mod:`app.services.auth_utils`. A leaked database row never reveals a
      usable code.
    * Generating a new code invalidates any earlier unused codes for the same
      user, so only the most recently emailed code is ever valid.
    * Verification is constant-time (:func:`secrets.compare_digest`) and is
      capped at ``TWO_FACTOR_MAX_ATTEMPTS`` wrong guesses before the code is
      burned, closing the online brute-force window.

The OIDC/SSO login path does not use this module: the identity provider
already enforces multi-factor and account status for ``@freightservices.net``
employees.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from flask import current_app, render_template_string, request

from app.models import EmailOtpToken, User, db
from app.services.mail import send_email

# Defaults used when the corresponding config key is missing or invalid. They
# match the documented values in ``docs/equations.md`` (EQ-019) and the README
# environment-variable table.
DEFAULT_CODE_LENGTH = 6
DEFAULT_CODE_TTL_MINUTES = 10
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RESEND_COOLDOWN_SECONDS = 30

TWO_FACTOR_FEATURE = "login_2fa"


def hash_login_code(code: str) -> str:
    """Return the SHA-256 hex digest stored for a login code.

    Args:
        code: Raw numeric code emailed to the user.

    Returns:
        Hex-encoded digest produced by :func:`hashlib.sha256`. Only the digest
        is persisted so leaked rows cannot be replayed.
    """

    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _config_int(key: str, default: int, *, minimum: int = 1) -> int:
    """Return a positive integer config value with a safe fallback.

    Args:
        key: ``current_app.config`` key to read.
        default: Value used when the key is missing or cannot be parsed.
        minimum: Lower bound enforced on the parsed value.

    Returns:
        The parsed integer, never below ``minimum``.
    """

    try:
        value = int(current_app.config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)


def code_length() -> int:
    """Return the configured number of digits in a login code."""

    return _config_int("TWO_FACTOR_CODE_LENGTH", DEFAULT_CODE_LENGTH, minimum=4)


def code_ttl() -> timedelta:
    """Return how long a generated code remains valid."""

    minutes = _config_int(
        "TWO_FACTOR_CODE_TTL_MINUTES", DEFAULT_CODE_TTL_MINUTES, minimum=1
    )
    return timedelta(minutes=minutes)


def max_attempts() -> int:
    """Return the number of wrong guesses allowed before a code is burned."""

    return _config_int("TWO_FACTOR_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, minimum=1)


def resend_cooldown() -> timedelta:
    """Return the minimum spacing between two emailed codes for one user."""

    seconds = _config_int(
        "TWO_FACTOR_RESEND_COOLDOWN_SECONDS",
        DEFAULT_RESEND_COOLDOWN_SECONDS,
        minimum=0,
    )
    return timedelta(seconds=seconds)


def two_factor_required(user: Optional[User]) -> bool:
    """Return whether ``user`` must clear an email code to finish logging in.

    Args:
        user: Authenticated :class:`~app.models.User` from the password check,
            or ``None``.

    Returns:
        ``True`` when the deployment-wide ``TWO_FACTOR_ENABLED`` flag is on and
        the account's :attr:`~app.models.User.two_factor_enabled` toggle has not
        been cleared by an administrator. ``None`` users never require a code.
    """

    if user is None:
        return False
    if not current_app.config.get("TWO_FACTOR_ENABLED", True):
        return False
    return bool(getattr(user, "two_factor_enabled", True))


def _generate_numeric_code(length: int) -> str:
    """Return a cryptographically random zero-padded numeric code.

    Args:
        length: Number of digits to produce.

    Returns:
        A string of ``length`` digits (leading zeros preserved) drawn from
        :func:`secrets.randbelow`, which is suitable for security tokens.
    """

    upper_bound = 10**length
    return str(secrets.randbelow(upper_bound)).zfill(length)


def _invalidate_outstanding_codes(user: User) -> None:
    """Mark every unused code for ``user`` as consumed.

    Ensures only the freshly generated code can be verified. Does not commit;
    the caller commits alongside the new row.
    """

    EmailOtpToken.query.filter_by(user_id=user.id, used=False).update(
        {EmailOtpToken.used: True}, synchronize_session=False
    )


def create_login_code(user: User) -> str:
    """Stage a fresh login code for ``user`` on the current transaction.

    Any earlier unused codes are invalidated first so only this code is live.
    The raw code is returned for delivery; only its hash is stored.

    **Does not commit.** The new token and the invalidation of earlier codes are
    added to the session but left uncommitted so the caller can treat code
    generation and email delivery as a single atomic unit — see
    :func:`start_login_challenge`, which commits only after the email is
    accepted and rolls back on failure. This keeps a failed send from
    invalidating the user's previous (still-valid) code or leaving an
    undelivered token behind.

    Args:
        user: Account that just passed the password check.

    Returns:
        The raw numeric code to email to the user.
    """

    _invalidate_outstanding_codes(user)
    code = _generate_numeric_code(code_length())
    token = EmailOtpToken(
        user_id=user.id,
        code_hash=hash_login_code(code),
        expires_at=datetime.utcnow() + code_ttl(),
    )
    db.session.add(token)
    return code


def _seconds_until_resend_allowed(user: User) -> int:
    """Return how many seconds remain before another code may be sent.

    Args:
        user: Account requesting a code.

    Returns:
        ``0`` when a new code may be sent immediately, otherwise the number of
        whole seconds the caller must wait. Based on the most recent code's
        ``created_at`` and the configured resend cooldown.
    """

    cooldown = resend_cooldown()
    if cooldown <= timedelta(0):
        return 0
    latest = (
        EmailOtpToken.query.filter_by(user_id=user.id)
        .order_by(EmailOtpToken.created_at.desc())
        .first()
    )
    if latest is None:
        return 0
    ready_at = latest.created_at + cooldown
    remaining = (ready_at - datetime.utcnow()).total_seconds()
    return max(0, int(remaining + 0.999))


_EMAIL_HTML_TEMPLATE = """
<h1>Hi {{ name }},</h1>
<p>Use the code below to finish signing in to your {{ product_name }} account.</p>
<p style="font-size:28px;font-weight:bold;letter-spacing:6px;margin:24px 0;color:#0f4c81;">{{ code }}</p>
<p>This code expires in {{ ttl_minutes }} minutes and can only be used once.</p>
<p>For security, this request came from a {{ operating_system }} device using {{ browser_name }}. If you did not just try to log in, you can ignore this email — your password is still required — and we recommend changing it as a precaution.</p>
<p>Thanks,<br>The {{ product_name }} Team</p>
"""


def send_login_code(user: User, code: str) -> None:
    """Email ``code`` to ``user`` using the shared mail service.

    Args:
        user: Recipient account.
        code: Raw numeric code produced by :func:`create_login_code`.

    Raises:
        MailRateLimitError: When the mail service throttles the send.
        smtplib.SMTPException: When SMTP delivery fails.

    External dependencies:
        * Delivers through :func:`app.services.mail.send_email`, which applies
          per-user/feature rate limits and logs the dispatch.
    """

    ttl_minutes = int(code_ttl().total_seconds() // 60)
    name = (getattr(user, "first_name", "") or "there").strip() or "there"
    try:
        user_agent = request.user_agent
        operating_system = user_agent.platform or "unknown"
        browser_name = user_agent.browser or "unknown"
    except RuntimeError:  # pragma: no cover - outside a request context
        operating_system = "unknown"
        browser_name = "unknown"

    template_vars = {
        "name": name,
        "product_name": "Freight Services",
        "code": code,
        "ttl_minutes": ttl_minutes,
        "operating_system": operating_system,
        "browser_name": browser_name,
    }
    html_body = render_template_string(_EMAIL_HTML_TEMPLATE, **template_vars)
    body = (
        f"Hi {name},\n\n"
        "Use the code below to finish signing in to your Freight Services "
        "account:\n\n"
        f"    {code}\n\n"
        f"This code expires in {ttl_minutes} minutes and can only be used "
        "once.\n\n"
        "If you did not just try to log in, you can ignore this email — your "
        "password is still required."
    )
    send_email(
        user.email,
        "Your Freight Services login code",
        body,
        feature=TWO_FACTOR_FEATURE,
        user=user,
        html_body=html_body,
    )


def start_login_challenge(user: User) -> Tuple[bool, Optional[str]]:
    """Generate and email a login code, honoring the resend cooldown.

    Args:
        user: Account that just passed the password check (or is requesting a
            resend from the verification page).

    Returns:
        Tuple ``(sent, error)``. ``sent`` is ``True`` when a code was emailed.
        When ``sent`` is ``False`` the ``error`` string explains why (currently
        only the resend cooldown). Mail-delivery failures propagate as
        exceptions so the caller can surface an appropriate message.

    Raises:
        MailRateLimitError: Propagated from :func:`send_login_code`.
        smtplib.SMTPException: Propagated from :func:`send_login_code`.

    External dependencies:
        * Treats code generation and delivery as one transaction: the new
          token (and the invalidation of earlier codes staged by
          :func:`create_login_code`) is committed only after
          :func:`send_login_code` returns. If the send raises, the transaction
          is rolled back so the user's previous code stays valid and no
          undelivered token is persisted.
    """

    wait_seconds = _seconds_until_resend_allowed(user)
    if wait_seconds > 0:
        return False, ("Please wait a few seconds before requesting another code.")
    code = create_login_code(user)
    try:
        send_login_code(user, code)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return True, None


def verify_login_code(user: User, submitted_code: str) -> Tuple[bool, Optional[str]]:
    """Validate a submitted login code for ``user``.

    Looks up the most recent unused code, enforces expiry and the
    wrong-guess cap, and consumes the code on success. The active code is
    burned once it expires or once ``TWO_FACTOR_MAX_ATTEMPTS`` wrong guesses
    accrue, so a fresh code must be requested in those cases.

    Args:
        user: Account being verified.
        submitted_code: Raw code typed by the user.

    Returns:
        Tuple ``(ok, error)`` where ``ok`` is ``True`` on a successful match
        (the code is marked used) and ``error`` is a human-readable message
        otherwise.
    """

    cleaned = (submitted_code or "").strip()
    # Lock the active token row for the duration of this transaction so two
    # concurrent verification requests can't both read the same ``attempts``
    # value and lose an increment — which would let an attacker slip extra
    # guesses past ``max_attempts``. On Postgres (production/test) this emits
    # ``SELECT ... FOR UPDATE``; on backends without row locks it degrades to a
    # plain select.
    token = (
        EmailOtpToken.query.filter_by(user_id=user.id, used=False)
        .order_by(EmailOtpToken.created_at.desc())
        .with_for_update()
        .first()
    )
    if token is None:
        return False, "That code is no longer valid. Request a new one."

    if token.expires_at < datetime.utcnow():
        token.used = True
        db.session.commit()
        return False, "That code has expired. Request a new one."

    if token.attempts >= max_attempts():
        token.used = True
        db.session.commit()
        return False, "Too many incorrect attempts. Request a new code."

    if not cleaned or not secrets.compare_digest(
        token.code_hash, hash_login_code(cleaned)
    ):
        token.attempts += 1
        remaining = max_attempts() - token.attempts
        if remaining <= 0:
            token.used = True
            db.session.commit()
            return False, "Too many incorrect attempts. Request a new code."
        db.session.commit()
        return False, "Incorrect code. Please try again."

    token.used = True
    db.session.commit()
    return True, None


def mask_email(email: str) -> str:
    """Return a partially masked email for display on the verification page.

    Args:
        email: Full address the code was sent to.

    Returns:
        A masked form such as ``j****n@example.com`` that confirms which
        mailbox to check without echoing the full address back to the browser.
    """

    address = (email or "").strip()
    if "@" not in address:
        return address
    local, domain = address.split("@", 1)
    if len(local) <= 2:
        masked_local = (local[0] if local else "") + "*"
    else:
        masked_local = f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}"
    return f"{masked_local}@{domain}"


__all__ = [
    "TWO_FACTOR_FEATURE",
    "code_length",
    "code_ttl",
    "create_login_code",
    "hash_login_code",
    "mask_email",
    "max_attempts",
    "send_login_code",
    "start_login_challenge",
    "two_factor_required",
    "verify_login_code",
]
