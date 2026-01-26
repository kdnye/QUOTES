from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

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
