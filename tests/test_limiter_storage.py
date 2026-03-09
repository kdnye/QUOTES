from __future__ import annotations

from unittest.mock import Mock

import pytest

from app import create_app


class LimiterStorageConfig:
    """Configuration fixture for limiter storage initialization tests.

    Inputs:
        None. Uses class attributes consumed by :func:`app.create_app`.

    Outputs:
        Provides Flask and SQLAlchemy settings for an in-memory test app.

    External dependencies:
        * Consumed by :func:`app.create_app` while initializing Flask
          extensions and startup checks.
    """

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    STARTUP_DB_CHECKS = False


def test_create_app_uses_session_redis_for_limiter_storage_when_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use ``SESSION_REDIS`` for limiter storage when Redis responds.

    Inputs:
        monkeypatch: Fixture used to patch environment variables and Redis
            client creation in :mod:`app`.

    Outputs:
        None. Assertions validate the selected limiter storage URI.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
        * Patches :func:`app.redispy.from_url` to return a Redis-like client
          with a working ``ping`` implementation.
    """

    monkeypatch.setenv("SESSION_REDIS", "redis://redis.internal:6379/9")
    redis_client = Mock()
    redis_client.ping.return_value = True
    monkeypatch.setattr("app.redispy.from_url", Mock(return_value=redis_client))

    flask_app = create_app(LimiterStorageConfig)

    assert flask_app.config["RATELIMIT_STORAGE_URI"] == "redis://redis.internal:6379/9"


def test_create_app_falls_back_to_memory_limiter_storage_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fallback to in-memory limiter counters when Redis is unavailable.

    Inputs:
        monkeypatch: Fixture used to patch environment variables and force
            Redis client initialization failure in :mod:`app`.
        caplog: Fixture that captures application log records for assertions.

    Outputs:
        None. Assertions validate fallback storage and warning logging.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
        * Patches :func:`app.redispy.from_url` to raise an exception.
    """

    monkeypatch.setenv("SESSION_REDIS", "redis://unavailable:6379/0")
    monkeypatch.setattr(
        "app.redispy.from_url", Mock(side_effect=RuntimeError("redis down"))
    )

    with caplog.at_level("WARNING"):
        flask_app = create_app(LimiterStorageConfig)

    assert flask_app.config["RATELIMIT_STORAGE_URI"] == "memory://"
    assert (
        "Redis unavailable for rate limiting; using in-memory counters" in caplog.text
    )
