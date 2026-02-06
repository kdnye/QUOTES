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


def test_validate_sender_domain_allows_empty_restriction(app: Flask) -> None:
    """Allow any sender domain when no restriction is configured.

    Args:
        app: Flask application fixture providing the configuration context.

    Returns:
        ``None``. The test asserts that no exception is raised.

    External dependencies:
        * Calls :func:`app.services.mail.validate_sender_domain`.
    """

    with app.app_context():
        app.config["MAIL_ALLOWED_SENDER_DOMAIN"] = ""
        mail_service.validate_sender_domain("quote@freightservices.net")
