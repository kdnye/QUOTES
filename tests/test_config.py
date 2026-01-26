from __future__ import annotations

import importlib
import logging
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from _pytest.logging import LogCaptureFixture

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

import config


def _reload_config(monkeypatch: Any) -> ModuleType:
    """Reload the config module after clearing environment markers.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        ModuleType: Reloaded configuration module.

    External Dependencies:
        Calls :func:`importlib.reload` on the imported :mod:`config` module.
    """

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    return importlib.reload(config)


def test_csrf_enabled_when_secret_key_is_set(monkeypatch: Any) -> None:
    """Assert CSRF protection stays enabled when a secret key is configured.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts CSRF protection stays enabled with a configured key.
    """

    monkeypatch.setenv("SECRET_KEY", "configured-secret")
    reloaded = _reload_config(monkeypatch)

    assert reloaded.Config.WTF_CSRF_ENABLED is True


def test_csrf_disabled_when_secret_key_is_missing(monkeypatch: Any) -> None:
    """Assert CSRF protection is disabled when the secret key is missing.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts CSRF protection is disabled without a configured key.
    """

    monkeypatch.delenv("SECRET_KEY", raising=False)
    reloaded = _reload_config(monkeypatch)

    assert reloaded.Config.WTF_CSRF_ENABLED is False


def test_config_import_does_not_create_instance_dir(monkeypatch: Any) -> None:
    """Ensure importing config does not create the instance directory.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts the instance directory stays absent after reload.

    External Dependencies:
        Removes directories with :func:`shutil.rmtree` and reloads the
        :mod:`config` module via :func:`importlib.reload`.
    """

    instance_dir = PROJECT_ROOT / "instance"
    if instance_dir.exists():
        shutil.rmtree(instance_dir)

    monkeypatch.delenv("SECRET_KEY", raising=False)
    _reload_config(monkeypatch)

    assert instance_dir.exists() is False


def test_config_falls_back_to_sqlite_when_database_missing(
    monkeypatch: Any,
    caplog: LogCaptureFixture,
) -> None:
    """Assert config import falls back to SQLite without DB configuration.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.
        caplog: Pytest fixture capturing log output for assertions.

    Returns:
        None. Asserts SQLite fallback and a warning log are emitted.

    External Dependencies:
        Reloads the :mod:`config` module via :func:`importlib.reload`.
    """

    for var in (
        "DATABASE_URL",
        "CLOUD_SQL_CONNECTION_NAME",
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_OPTIONS",
    ):
        monkeypatch.delenv(var, raising=False)

    with caplog.at_level(logging.WARNING, logger="quote_tool.config"):
        reloaded = importlib.reload(config)

    assert (
        reloaded.Config.SQLALCHEMY_DATABASE_URI
        == f"sqlite:///{reloaded.DEFAULT_DB_PATH}"
    )
    assert (
        "No database configuration found; falling back to local SQLite to "
        "enable Setup Mode."
        in caplog.text
    )


def test_cloud_run_defaults_to_local_branding_storage_without_bucket(
    monkeypatch: Any,
) -> None:
    """Ensure branding storage falls back to local without a Cloud Run bucket.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts the branding storage backend falls back to ``local``.
    """

    monkeypatch.setenv("K_SERVICE", "quote-tool")
    monkeypatch.delenv("BRANDING_STORAGE", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    reloaded = _reload_config(monkeypatch)

    assert reloaded.Config.BRANDING_STORAGE == "local"
    assert reloaded.Config.CONFIG_ERRORS == []


def test_explicit_gcs_branding_without_bucket_falls_back_to_local(
    monkeypatch: Any,
) -> None:
    """Ensure explicit GCS branding storage falls back without a bucket.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts explicit GCS branding storage becomes ``local`` when the
        bucket is missing.
    """

    monkeypatch.setenv("K_SERVICE", "quote-tool")
    monkeypatch.setenv("BRANDING_STORAGE", "gcs")
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    reloaded = _reload_config(monkeypatch)

    assert reloaded.Config.BRANDING_STORAGE == "local"
    assert reloaded.Config.CONFIG_ERRORS == []
