from __future__ import annotations

"""Utility helpers for outbound email policies and rate limiting."""

import random
import re
import smtplib
import time
from html import escape
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Mapping, Optional, Tuple, Type

from flask import current_app

from app.models import EmailDispatchLog, User, db


class MailRateLimitError(RuntimeError):
    """Raised when an outbound email exceeds configured rate limits."""


def _build_professional_html_email(subject: str, body: str) -> str:
    """Build a polished HTML email from plain-text inputs.

    Args:
        subject: Email subject line shown in the message header.
        body: Plain-text body content supplied by the caller.

    Returns:
        A full HTML string suitable for ``EmailMessage.add_alternative``.

    External dependencies:
        * Uses :func:`html.escape` to safely render untrusted text.
        * Uses :func:`re.sub` to detect and linkify HTTP(S) URLs in the body.
    """

    escaped_subject = escape(subject)
    escaped_body = escape(body)
    linked_body = re.sub(
        r"(https?://[^\s<]+)",
        lambda match: (
            '<a href="{0}" style="color:#0f4c81;text-decoration:underline;">' "{0}</a>"
        ).format(match.group(1)),
        escaped_body,
    )
    formatted_body = linked_body.replace("\n", "<br>")

    return (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escaped_subject}</title>"
        "</head>"
        '<body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,sans-serif;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="background:#f4f6f8;padding:24px 12px;">'
        "<tr>"
        '<td align="center">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="max-width:640px;background:#ffffff;border-radius:12px;'
        'border:1px solid #d9e0e7;overflow:hidden;">'
        "<tr>"
        '<td style="padding:24px 28px;background:#0f4c81;color:#ffffff;">'
        '<p style="margin:0;font-size:12px;letter-spacing:1.2px;text-transform:uppercase;">'
        "Freight Services</p>"
        f'<h1 style="margin:8px 0 0;font-size:20px;line-height:1.3;">{escaped_subject}</h1>'
        "</td>"
        "</tr>"
        "<tr>"
        '<td style="padding:24px 28px;color:#1f2d3d;font-size:15px;line-height:1.7;">'
        f"{formatted_body}"
        "</td>"
        "</tr>"
        "<tr>"
        '<td style="padding:16px 28px 24px;color:#6b7785;font-size:12px;line-height:1.5;'
        'border-top:1px solid #e5ebf0;">'
        "This is an automated message from Freight Services."
        "</td>"
        "</tr>"
        "</table>"
        "</td>"
        "</tr>"
        "</table>"
        "</body>"
        "</html>"
    )


def _normalize_feature(feature: Optional[str]) -> str:
    """Return a normalized feature label used for logging."""

    value = (feature or "general").strip().lower()
    return value or "general"


def _normalize_recipient(recipient: str) -> str:
    """Return a lowercase recipient address for consistent lookups."""

    return recipient.strip().lower()


def validate_sender_domain(sender: str) -> None:
    """Validate that ``sender`` includes a usable domain segment.

    Args:
        sender: Email address configured as ``MAIL_DEFAULT_SENDER``.

    Returns:
        ``None``. This helper raises only on invalid configuration.

    Raises:
        ValueError: If the address is missing an ``@`` symbol.

    External dependencies:
        * None.
    """

    if "@" not in sender:
        raise ValueError("MAIL_DEFAULT_SENDER must include a domain component.")


def user_has_mail_privileges(user: Optional[User]) -> bool:
    """Return ``True`` when ``user`` is cleared to use staff-only mail features.

    Args:
        user: The authenticated :class:`app.models.User` or ``None`` for
            anonymous visitors.

    Returns:
        ``True`` if the account has opted into email privileges via the
        ``can_send_mail`` toggle or when the caller is an approved employee or
        super administrator. Anonymous users and unapproved employees are
        denied access to advanced mail features.

    External dependencies:
        * Relies on :class:`app.models.User` for ``can_send_mail`` and role data.
    """

    if not user:
        return False

    if getattr(user, "can_send_mail", False):
        return True

    role = (getattr(user, "role", "") or "").lower()
    if role == "super_admin":
        return True
    if role == "employee":
        return bool(getattr(user, "employee_approved", False))

    return False


def enforce_mail_rate_limit(feature: str, user: Optional[User], recipient: str) -> None:
    """Validate that ``user`` and ``feature`` have not exceeded limits.

    Args:
        feature: Human readable label describing the caller, e.g.
            ``"password_reset"``. Used for aggregate caps.
        user: Authenticated :class:`app.models.User` initiating the send or
            ``None`` when called anonymously.
        recipient: Email address receiving the message. Used to prevent a
            single address from being spammed.

    Raises:
        MailRateLimitError: If per-user, per-feature, or per-recipient caps are
            exceeded.
    """

    feature_key = _normalize_feature(feature)
    recipient_key = _normalize_recipient(recipient)
    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)

    per_user_hour = int(current_app.config.get("MAIL_RATE_LIMIT_PER_USER_PER_HOUR", 0))
    per_user_day = int(current_app.config.get("MAIL_RATE_LIMIT_PER_USER_PER_DAY", 0))
    per_feature_hour = int(
        current_app.config.get("MAIL_RATE_LIMIT_PER_FEATURE_PER_HOUR", 0)
    )
    per_recipient_day = int(
        current_app.config.get("MAIL_RATE_LIMIT_PER_RECIPIENT_PER_DAY", 0)
    )

    if user and per_user_hour > 0:
        count = EmailDispatchLog.query.filter(
            EmailDispatchLog.feature == feature_key,
            EmailDispatchLog.user_id == user.id,
            EmailDispatchLog.created_at >= hour_ago,
        ).count()
        if count >= per_user_hour:
            raise MailRateLimitError(
                "Hourly email limit reached for your account. "
                "Please wait before sending another message."
            )

    if user and per_user_day > 0:
        count = EmailDispatchLog.query.filter(
            EmailDispatchLog.feature == feature_key,
            EmailDispatchLog.user_id == user.id,
            EmailDispatchLog.created_at >= day_ago,
        ).count()
        if count >= per_user_day:
            raise MailRateLimitError(
                "Daily email limit reached for your account. "
                "Please try again tomorrow."
            )

    if per_recipient_day > 0:
        count = EmailDispatchLog.query.filter(
            EmailDispatchLog.feature == feature_key,
            EmailDispatchLog.recipient == recipient_key,
            EmailDispatchLog.created_at >= day_ago,
        ).count()
        if count >= per_recipient_day:
            raise MailRateLimitError(
                "Too many emails have been sent to this recipient today. "
                "Please try again tomorrow."
            )

    if per_feature_hour > 0:
        count = EmailDispatchLog.query.filter(
            EmailDispatchLog.feature == feature_key,
            EmailDispatchLog.created_at >= hour_ago,
        ).count()
        if count >= per_feature_hour:
            raise MailRateLimitError(
                "Email delivery is temporarily paused due to high volume. "
                "Please retry in a few minutes."
            )


def log_email_dispatch(feature: str, user: Optional[User], recipient: str) -> None:
    """Persist a log entry after a successful send.

    Args:
        feature: Same label supplied to :func:`enforce_mail_rate_limit`.
        user: Optional :class:`app.models.User` associated with the action.
        recipient: Target email address.
    """

    entry = EmailDispatchLog(
        feature=_normalize_feature(feature),
        recipient=_normalize_recipient(recipient),
        user_id=user.id if user else None,
    )
    db.session.add(entry)
    db.session.commit()


__all__ = [
    "MailRateLimitError",
    "enforce_mail_rate_limit",
    "log_email_dispatch",
    "send_email",
    "user_has_mail_privileges",
    "validate_sender_domain",
]


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    feature: str = "general",
    user: Optional[User] = None,
    headers: Optional[Mapping[str, str]] = None,
    html_body: Optional[str] = None,
) -> None:
    """Send an email using SMTP after enforcing safety policies.

    Args:
        to: Recipient email address.
        subject: Message subject line.
        body: Plain-text message body.
        feature: Short label identifying the caller (for example,
            ``"password_reset"``). Used by
            :func:`services.mail.enforce_mail_rate_limit` to track usage.
        user: Authenticated :class:`~app.models.User` requesting the send, if
            available. Enables per-user throttles.
        headers: Optional message headers (for example ``List-Unsubscribe``)
            to attach before delivery.
        html_body: Optional pre-rendered HTML payload. When provided, this
            bypasses :func:`services.mail._build_professional_html_email` so
            flows such as password-reset can send purpose-built markup.

    Raises:
        MailRateLimitError: When rate limits configured in
            :mod:`services.mail` are exceeded.
        ValueError: If ``MAIL_DEFAULT_SENDER`` is missing a domain component.
        smtplib.SMTPException: If the underlying SMTP call fails.
        smtplib.SMTPServerDisconnected: When a transient SMTP disconnect
            persists after retries.
        smtplib.SMTPResponseException: When a transient SMTP response error
            persists after retries.
        smtplib.SMTPConnectError: When a transient SMTP connection error
            persists after retries.

    External dependencies:
        * Applies :func:`services.mail.enforce_mail_rate_limit` and
          :func:`services.mail.log_email_dispatch` around SMTP activity.
        * Reads runtime overrides with :func:`services.settings.load_mail_settings`.
        * Retries transient SMTP failures with backoff using
          :func:`time.sleep` and jitter from :func:`random.uniform`.
    """

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["X-PM-Message-Stream"] = "quote-tool"
    default_sender = current_app.config.get(
        "MAIL_DEFAULT_SENDER", "quote@freightservices.net"
    )
    validate_sender_domain(default_sender)
    msg["From"] = default_sender
    msg["To"] = to
    if headers:
        for header_name, header_value in headers.items():
            msg[header_name] = header_value
    msg.set_content(body)
    final_html = html_body or _build_professional_html_email(subject, body)
    msg.add_alternative(final_html, subtype="html")
    enforce_mail_rate_limit(feature, user, to)

    overrides = None
    try:
        from app.services.settings import load_mail_settings

        overrides = load_mail_settings()
    except Exception:
        overrides = None

    server = (
        overrides.server
        if getattr(overrides, "server", None)
        else current_app.config.get("MAIL_SERVER", "localhost")
    )
    configured_port = current_app.config.get("MAIL_PORT", 0) or None
    port = (
        overrides.port
        if getattr(overrides, "port", None) is not None
        else configured_port
    )
    use_tls = (
        overrides.use_tls
        if getattr(overrides, "use_tls", None) is not None
        else current_app.config.get("MAIL_USE_TLS")
    )
    use_ssl = (
        overrides.use_ssl
        if getattr(overrides, "use_ssl", None) is not None
        else current_app.config.get("MAIL_USE_SSL")
    )
    username = (
        overrides.username
        if getattr(overrides, "username", None)
        else current_app.config.get("MAIL_USERNAME")
    )
    password = (
        overrides.password
        if getattr(overrides, "password", None)
        else current_app.config.get("MAIL_PASSWORD")
    )

    if use_ssl:
        smtp_cls = smtplib.SMTP_SSL
        default_port = 465
    else:
        smtp_cls = smtplib.SMTP
        default_port = 587 if use_tls else 25

    def _deliver_message(
        smtp_type: Type[smtplib.SMTP],
        host: str,
        host_port: Optional[int],
        fallback_port: int,
        enable_tls: bool,
        enable_ssl: bool,
        smtp_user: Optional[str],
        smtp_password: Optional[str],
    ) -> None:
        """Connect and send the email over SMTP.

        Args:
            smtp_type: SMTP class to instantiate (``smtplib.SMTP`` or
                ``smtplib.SMTP_SSL``).
            host: Mail server hostname.
            host_port: Explicit server port, if configured.
            fallback_port: Port to use when ``host_port`` is ``None``.
            enable_tls: Whether to start TLS after connecting.
            enable_ssl: Whether the connection is already wrapped in SSL.
            smtp_user: Optional username to authenticate with the server.
            smtp_password: Optional password to authenticate with the server.

        Returns:
            ``None`` after the message is sent successfully.

        External dependencies:
            * Uses :class:`smtplib.SMTP` or :class:`smtplib.SMTP_SSL` to
              connect to the mail server.
        """

        with smtp_type(host, host_port or fallback_port) as smtp:
            if enable_tls and not enable_ssl:
                smtp.starttls()
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)

    def _retry_delay_seconds(attempt: int, base_delays: Tuple[float, ...]) -> float:
        """Return a jittered delay for a retry attempt.

        Args:
            attempt: Zero-based retry attempt index.
            base_delays: Ordered sequence of base delay durations in seconds.

        Returns:
            Delay in seconds that includes a small random jitter.

        External dependencies:
            * Uses :func:`random.uniform` from the standard library.
        """

        base_delay = base_delays[min(attempt, len(base_delays) - 1)]
        jitter = random.uniform(0, base_delay * 0.1)
        return base_delay + jitter

    retry_delays = (0.5, 1.0, 2.0)
    transient_errors = (
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPResponseException,
        smtplib.SMTPConnectError,
    )
    max_attempts = len(retry_delays) + 1

    for attempt in range(max_attempts):
        try:
            _deliver_message(
                smtp_cls,
                server,
                port,
                default_port,
                use_tls,
                use_ssl,
                username,
                password,
            )
            break
        except transient_errors as exc:
            if attempt >= max_attempts - 1:
                raise
            delay = _retry_delay_seconds(attempt, retry_delays)
            current_app.logger.warning(
                "Transient SMTP failure (%s). Retrying in %.2fs (attempt %s/%s).",
                exc.__class__.__name__,
                delay,
                attempt + 1,
                max_attempts,
            )
            time.sleep(delay)
    log_email_dispatch(feature, user, to)
