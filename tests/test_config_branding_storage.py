from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))


def _reload_config(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> ModuleType:
    """Reload the config module after applying environment overrides.

    Args:
        monkeypatch: Pytest fixture used to safely override environment values.
        env: Environment variables to apply for the reload.

    Returns:
        Reloaded ``config`` module with the new environment applied.

    External Dependencies:
        Calls :func:`importlib.reload` and relies on ``config`` reading values
        via :func:`os.getenv`.
    """

    for key in (
        "BRANDING_STORAGE",
        "GCS_BUCKET",
        "K_SERVICE",
        "K_REVISION",
        "ENVIRONMENT",
        "FLASK_ENV",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import config as config_module

    return importlib.reload(config_module)


def test_resolve_branding_storage_prefers_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure explicit ``BRANDING_STORAGE`` overrides defaults."""

    config = _reload_config(monkeypatch, {"BRANDING_STORAGE": "Local"})

    assert config._resolve_branding_storage() == "local"
    assert config.Config.BRANDING_STORAGE == "local"


def test_resolve_branding_storage_cloud_run_defaults_gcs_with_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm Cloud Run defaults to GCS when a bucket is configured."""

    config = _reload_config(
        monkeypatch, {"K_SERVICE": "quotes", "GCS_BUCKET": "branding-bucket"}
    )

    assert config._resolve_branding_storage() == "gcs"
    assert config.Config.BRANDING_STORAGE == "gcs"
    assert config.Config.CONFIG_ERRORS == []


def test_resolve_branding_storage_cloud_run_defaults_local_without_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm Cloud Run uses local storage when no bucket is configured."""

    config = _reload_config(monkeypatch, {"K_SERVICE": "quotes"})

    assert config._resolve_branding_storage() == "local"
    assert config.Config.BRANDING_STORAGE == "local"
    assert config.Config.CONFIG_ERRORS == []


def test_gcs_bucket_required_when_branding_storage_is_gcs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure missing ``GCS_BUCKET`` is reported when storage is GCS."""

    config = _reload_config(monkeypatch, {"BRANDING_STORAGE": "gcs"})

    assert (
        "GCS_BUCKET must be set when BRANDING_STORAGE is configured for GCS."
        in config.Config.CONFIG_ERRORS
    )
