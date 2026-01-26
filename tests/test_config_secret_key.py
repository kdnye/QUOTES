from __future__ import annotations

import importlib
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
    """

    delenv = getattr(monkeypatch, "delenv")
    delenv("ENVIRONMENT", raising=False)
    delenv("FLASK_ENV", raising=False)
    return importlib.reload(config)


def test_secret_key_flag_is_false_when_configured(monkeypatch) -> None:
    """Ensure configured secret keys disable the temporary flag and keep CSRF.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts the configured key is respected and CSRF stays enabled.
    """

    monkeypatch.setenv("SECRET_KEY", "configured-secret")
    reloaded = _reload_config(monkeypatch)

    assert reloaded.Config.SECRET_KEY == "configured-secret"
    assert reloaded.Config.SECRET_KEY_IS_TEMPORARY is False
    assert reloaded.Config.WTF_CSRF_ENABLED is True


def test_secret_key_flag_is_true_when_missing(monkeypatch) -> None:
    """Ensure missing secret keys set the temporary flag and disable CSRF.

    Args:
        monkeypatch: Pytest fixture for safely patching environment variables.

    Returns:
        None. Asserts a generated key is present and CSRF is disabled.
    """

    monkeypatch.delenv("SECRET_KEY", raising=False)
    reloaded = _reload_config(monkeypatch)

    assert reloaded.Config.SECRET_KEY
    assert reloaded.Config.SECRET_KEY_IS_TEMPORARY is True
    assert reloaded.Config.WTF_CSRF_ENABLED is False
