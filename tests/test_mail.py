from __future__ import annotations

import logging
import smtplib

import pytest
from flask import Flask

from app import create_app
from app.models import db
from app.services import mail as mail_service


class TestMailConfig:
    """Configuration overrides for mail retry tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = True
    MAIL_DEFAULT_SENDER = "quote@freightservices.net"
    MAIL_SERVER = "smtp.example.com"
    MAIL_PORT = 25
    MAIL_USE_TLS = False
    MAIL_USE_SSL = False


@pytest.fixture()
def app(postgres_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create a Flask app wired to a PostgreSQL test database.

    Args:
        postgres_database_url: PostgreSQL connection string for tests.
        monkeypatch: Pytest fixture for environment overrides.

    Returns:
        A configured Flask application for tests.

    External dependencies:
        * Sets ``MIGRATE_ON_STARTUP`` via :func:`monkeypatch.setenv`.
        * Calls :func:`app.create_app` to build the Flask application.
    """

    TestMailConfig.SQLALCHEMY_DATABASE_URI = postgres_database_url
    monkeypatch.setenv("MIGRATE_ON_STARTUP", "true")
    app = create_app(TestMailConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def test_send_email_retries_transient_error(
    app: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Retry once when a transient SMTP error occurs, then succeed."""

    attempts = {"count": 0}
    sleep_calls: list[float] = []

    class FakeSMTP:
        """Minimal SMTP fake to trigger a transient error then succeed."""

        def __init__(self, host: str, port: int) -> None:
            """Store connection metadata for assertions.

            Args:
                host: SMTP hostname requested by the mail helper.
                port: SMTP port requested by the mail helper.

            Returns:
                ``None``. The instance retains the arguments for inspection.

            External dependencies:
                * Mimics :class:`smtplib.SMTP` behavior for tests.
            """

            self.host = host
            self.port = port

        def __enter__(self) -> "FakeSMTP":
            """Return the SMTP fake for context manager usage.

            Returns:
                ``self`` so the caller can call ``send_message``.

            External dependencies:
                * Implements the context manager protocol used by
                  :func:`app.services.mail.send_email`.
            """

            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> bool:
            """Exit the context manager without suppressing exceptions.

            Args:
                exc_type: Exception type raised inside the context, if any.
                exc: Exception instance raised inside the context, if any.
                tb: Traceback for the raised exception, if any.

            Returns:
                ``False`` to propagate exceptions to the caller.

            External dependencies:
                * Matches the context manager contract expected by
                  :class:`smtplib.SMTP`.
            """

            return False

        def starttls(self) -> None:
            """No-op TLS upgrade for compatibility with the mail helper.

            Returns:
                ``None`` because the fake SMTP server does not perform TLS.

            External dependencies:
                * Mirrors the :meth:`smtplib.SMTP.starttls` interface.
            """

            return None

        def login(self, user: str, password: str) -> None:
            """No-op authentication for compatibility with the mail helper.

            Args:
                user: Username provided by the mail helper.
                password: Password provided by the mail helper.

            Returns:
                ``None`` because authentication is not simulated.

            External dependencies:
                * Mirrors the :meth:`smtplib.SMTP.login` interface.
            """

            return None

        def send_message(self, message: object) -> None:
            """Raise once, then succeed to simulate transient errors.

            Args:
                message: Email message object passed by the mail helper.

            Returns:
                ``None`` on the second call to indicate success.

            External dependencies:
                * Raises :class:`smtplib.SMTPServerDisconnected` to simulate a
                  transient SMTP failure.
            """

            attempts["count"] += 1
            if attempts["count"] == 1:
                raise smtplib.SMTPServerDisconnected("lost connection")

    monkeypatch.setattr(mail_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail_service.random, "uniform", lambda *_: 0.0)
    monkeypatch.setattr(
        mail_service.time, "sleep", lambda delay: sleep_calls.append(delay)
    )

    with app.app_context():
        caplog.set_level(logging.WARNING)
        mail_service.send_email(
            "recipient@example.com",
            "Subject",
            "Body",
        )

    assert attempts["count"] == 2
    assert sleep_calls == [0.5]
    assert (
        "Transient SMTP failure" in caplog.records[0].getMessage()
        if caplog.records
        else False
    )


def test_send_email_raises_after_retries(
    app: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Raise the final transient SMTP error after retries are exhausted."""

    attempts = {"count": 0}
    sleep_calls: list[float] = []

    class AlwaysFailSMTP:
        """SMTP fake that always triggers a transient connection error."""

        def __init__(self, host: str, port: int) -> None:
            """Store connection metadata for assertions.

            Args:
                host: SMTP hostname requested by the mail helper.
                port: SMTP port requested by the mail helper.

            Returns:
                ``None``. The instance retains the arguments for inspection.

            External dependencies:
                * Mimics :class:`smtplib.SMTP` behavior for tests.
            """

            self.host = host
            self.port = port

        def __enter__(self) -> "AlwaysFailSMTP":
            """Return the SMTP fake for context manager usage.

            Returns:
                ``self`` so the caller can call ``send_message``.

            External dependencies:
                * Implements the context manager protocol used by
                  :func:`app.services.mail.send_email`.
            """

            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> bool:
            """Exit the context manager without suppressing exceptions.

            Args:
                exc_type: Exception type raised inside the context, if any.
                exc: Exception instance raised inside the context, if any.
                tb: Traceback for the raised exception, if any.

            Returns:
                ``False`` to propagate exceptions to the caller.

            External dependencies:
                * Matches the context manager contract expected by
                  :class:`smtplib.SMTP`.
            """

            return False

        def starttls(self) -> None:
            """No-op TLS upgrade for compatibility with the mail helper.

            Returns:
                ``None`` because the fake SMTP server does not perform TLS.

            External dependencies:
                * Mirrors the :meth:`smtplib.SMTP.starttls` interface.
            """

            return None

        def login(self, user: str, password: str) -> None:
            """No-op authentication for compatibility with the mail helper.

            Args:
                user: Username provided by the mail helper.
                password: Password provided by the mail helper.

            Returns:
                ``None`` because authentication is not simulated.

            External dependencies:
                * Mirrors the :meth:`smtplib.SMTP.login` interface.
            """

            return None

        def send_message(self, message: object) -> None:
            """Always raise a transient SMTP connection error.

            Args:
                message: Email message object passed by the mail helper.

            Returns:
                ``None`` because the fake always fails by raising.

            External dependencies:
                * Raises :class:`smtplib.SMTPConnectError` to simulate
                  a transient connection failure.
            """

            attempts["count"] += 1
            raise smtplib.SMTPConnectError(421, "service not available")

    monkeypatch.setattr(mail_service.smtplib, "SMTP", AlwaysFailSMTP)
    monkeypatch.setattr(mail_service.random, "uniform", lambda *_: 0.0)
    monkeypatch.setattr(
        mail_service.time, "sleep", lambda delay: sleep_calls.append(delay)
    )

    with app.app_context():
        caplog.set_level(logging.WARNING)
        with pytest.raises(smtplib.SMTPConnectError):
            mail_service.send_email(
                "recipient@example.com",
                "Subject",
                "Body",
            )

    assert attempts["count"] == 4
    assert sleep_calls == [0.5, 1.0, 2.0]
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 3


def test_validate_sender_domain_accepts_valid_address(app: Flask) -> None:
    """Accept sender addresses that include a domain component.

    Args:
        app: Flask application fixture providing the configuration context.

    Returns:
        ``None``. The test asserts that no exception is raised.

    External dependencies:
        * Calls :func:`app.services.mail.validate_sender_domain`.
    """

    with app.app_context():
        mail_service.validate_sender_domain("quote@freightservices.net")


def test_validate_sender_domain_rejects_missing_at_symbol(app: Flask) -> None:
    """Reject sender addresses without a domain component.

    Args:
        app: Flask application fixture providing the configuration context.

    Returns:
        ``None``. The assertion verifies a validation error is raised.

    External dependencies:
        * Calls :func:`app.services.mail.validate_sender_domain`.
    """

    with app.app_context():
        with pytest.raises(ValueError):
            mail_service.validate_sender_domain("missing-domain")


def test_send_email_includes_html_alternative(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attach a styled HTML alternative for SMTP clients that support HTML.

    Args:
        app: Flask application fixture providing configuration and DB context.
        monkeypatch: Fixture used to replace SMTP calls in-process.

    Returns:
        ``None``. Assertions validate the generated MIME parts.

    External dependencies:
        * Calls :func:`app.services.mail.send_email`.
        * Uses :class:`email.message.EmailMessage` APIs exposed by stdlib.
    """

    captured: dict[str, object] = {}

    class CaptureSMTP:
        """SMTP fake that captures the outbound message for assertions."""

        def __init__(self, host: str, port: int) -> None:
            """Store host and port for parity with the real SMTP class.

            Args:
                host: SMTP hostname requested by the mail helper.
                port: SMTP port requested by the mail helper.

            Returns:
                ``None``. Values are captured for debug completeness.

            External dependencies:
                * Mirrors :class:`smtplib.SMTP` constructor signature.
            """

            captured["host"] = host
            captured["port"] = port

        def __enter__(self) -> "CaptureSMTP":
            """Return self for context-managed SMTP interactions.

            Returns:
                ``self`` so ``send_message`` can be invoked.

            External dependencies:
                * Implements the context protocol used by
                  :func:`app.services.mail.send_email`.
            """

            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> bool:
            """Exit without suppressing exceptions.

            Args:
                exc_type: Exception type raised within the context, if any.
                exc: Exception instance raised within the context, if any.
                tb: Traceback object for the exception, if any.

            Returns:
                ``False`` to preserve normal exception propagation.

            External dependencies:
                * Mirrors :class:`smtplib.SMTP` context manager behavior.
            """

            return False

        def starttls(self) -> None:
            """No-op TLS hook for compatibility with the SMTP interface.

            Returns:
                ``None``.

            External dependencies:
                * Matches :meth:`smtplib.SMTP.starttls`.
            """

            return None

        def login(self, user: str, password: str) -> None:
            """No-op login hook for compatibility with the SMTP interface.

            Args:
                user: Username provided by the mail helper.
                password: Password provided by the mail helper.

            Returns:
                ``None``.

            External dependencies:
                * Matches :meth:`smtplib.SMTP.login`.
            """

            return None

        def send_message(self, message: object) -> None:
            """Capture the final message object sent by the mail helper.

            Args:
                message: Email message object produced by the helper.

            Returns:
                ``None`` after storing the object for assertions.

            External dependencies:
                * Receives payload from :func:`app.services.mail.send_email`.
            """

            captured["message"] = message

    monkeypatch.setattr(mail_service.smtplib, "SMTP", CaptureSMTP)

    with app.app_context():
        mail_service.send_email(
            "recipient@example.com",
            "Quote Ready",
            "Review your quote at https://example.com/quotes/123\nThanks!",
        )

    message = captured["message"]
    assert hasattr(message, "is_multipart")
    assert message.is_multipart() is True
    assert message.get_content_type() == "multipart/alternative"

    plain_part, html_part = message.iter_parts()
    assert plain_part.get_content_type() == "text/plain"
    assert html_part.get_content_type() == "text/html"
    assert (
        "Review your quote at https://example.com/quotes/123"
        in plain_part.get_content()
    )
    html_content = html_part.get_content()
    assert "Freight Services" in html_content
    assert '<a href="https://example.com/quotes/123"' in html_content


def test_send_email_uses_custom_html_body(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prefer explicit HTML content over the default Freight Services wrapper.

    Args:
        app: Flask application fixture providing configuration and DB context.
        monkeypatch: Fixture used to replace SMTP calls in-process.

    Returns:
        ``None``. Assertions validate custom HTML is attached unchanged.

    External dependencies:
        * Calls :func:`app.services.mail.send_email` with ``html_body``.
        * Uses :class:`email.message.EmailMessage` MIME helpers from stdlib.
    """

    captured: dict[str, object] = {}

    class CaptureSMTP:
        """SMTP fake that captures the outbound message for assertions."""

        def __init__(self, host: str, port: int) -> None:
            """Store host and port for parity with the real SMTP class.

            Args:
                host: SMTP hostname requested by the mail helper.
                port: SMTP port requested by the mail helper.

            Returns:
                ``None``.

            External dependencies:
                * Mirrors :class:`smtplib.SMTP` constructor signature.
            """

            captured["host"] = host
            captured["port"] = port

        def __enter__(self) -> "CaptureSMTP":
            """Return self for context-managed SMTP interactions.

            Returns:
                ``self`` so ``send_message`` can be invoked.
            """

            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> bool:
            """Exit without suppressing exceptions.

            Returns:
                ``False`` to preserve normal exception propagation.
            """

            return False

        def starttls(self) -> None:
            """No-op TLS hook for compatibility with the SMTP interface."""

            return None

        def login(self, user: str, password: str) -> None:
            """No-op login hook for compatibility with the SMTP interface."""

            return None

        def send_message(self, message: object) -> None:
            """Capture the final message object sent by the mail helper."""

            captured["message"] = message

    custom_html = "<h1>Custom reset email</h1><p>Keep this markup.</p>"

    monkeypatch.setattr(mail_service.smtplib, "SMTP", CaptureSMTP)

    with app.app_context():
        mail_service.send_email(
            "recipient@example.com",
            "Quote Ready",
            "Fallback text body",
            html_body=custom_html,
        )

    message = captured["message"]
    plain_part, html_part = message.iter_parts()
    assert "Fallback text body" in plain_part.get_content()
    assert html_part.get_content().strip() == custom_html
