"""Helpers for persisted runtime configuration overrides.

The module centralizes interaction with the ``app_settings`` table so features
can read and modify runtime configuration without worrying about caching,
parsing, or missing database tables. Settings are loaded during application
startup and applied to :class:`flask.Flask.config` so rate limiting and other
behaviour can react to overrides immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Union

from flask import Flask
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.models import AppSetting, db

LOGGER = logging.getLogger(__name__)

SettingValue = Union[str, int, bool]


@dataclass(frozen=True)
class SettingRecord:
    """Snapshot of a configuration override stored in the database.

    Attributes:
        id: Primary key of the underlying :class:`AppSetting` row.
        key: Normalized identifier stored in :class:`AppSetting.key`.
        raw_value: Trimmed string persisted in :class:`AppSetting.value`.
        is_secret: Whether the value should be hidden from non-sensitive UIs.
        parsed_value: Deserialized Python value applied to ``app.config``.
        updated_at: Timestamp recorded on the :class:`AppSetting` row.
    """

    id: Optional[int]
    key: str
    raw_value: Optional[str]
    is_secret: bool
    parsed_value: Optional[SettingValue]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class MailSettings:
    """Structured SMTP overrides stored in :class:`AppSetting` rows."""

    server: Optional[str] = None
    port: Optional[int] = None
    use_tls: Optional[bool] = None
    use_ssl: Optional[bool] = None
    username: Optional[str] = None
    password: Optional[str] = None


_MAIL_SETTING_KEYS = (
    "mail_server",
    "mail_port",
    "mail_use_tls",
    "mail_use_ssl",
    "mail_username",
    "mail_password",
)

_TRUE_VALUES = {"true", "1", "yes", "y", "on"}
_FALSE_VALUES = {"false", "0", "no", "n", "off"}
_SETTINGS_CACHE: Dict[str, SettingRecord] = {}
_BASELINE_CONFIG: Dict[str, Any] = {}
_APPLIED_CONFIG_KEYS: Set[str] = set()
_MISSING = object()


def _normalize_key(key: str) -> str:
    """Return the canonical form used for :class:`AppSetting.key`."""

    return key.strip().lower()


def _clean_value(value: Optional[str]) -> Optional[str]:
    """Normalize user input strings."""

    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    """Convert stored strings into boolean flags."""

    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    """Convert persisted values to integers."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _deserialize(value: Optional[str]) -> Optional[SettingValue]:
    """Convert a stored string into a Python value."""

    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    parsed_bool = _parse_bool(cleaned)
    if parsed_bool is not None:
        return parsed_bool
    parsed_int = _parse_int(cleaned)
    if parsed_int is not None:
        return parsed_int
    return cleaned


def _snapshot(row: AppSetting) -> SettingRecord:
    """Create a :class:`SettingRecord` for ``row``."""

    cleaned = _clean_value(row.value)
    return SettingRecord(
        id=row.id,
        key=row.key,
        raw_value=cleaned,
        is_secret=bool(row.is_secret),
        parsed_value=_deserialize(cleaned),
        updated_at=row.updated_at,
    )


def get_settings_cache() -> Dict[str, SettingRecord]:
    """Return cached overrides, loading them from the database if necessary."""

    if not _SETTINGS_CACHE:
        refresh_settings_cache()
    return dict(_SETTINGS_CACHE)


def refresh_settings_cache() -> Dict[str, SettingRecord]:
    """Reload overrides from the database into the in-memory cache.

    The helper tolerates missing tables so fresh deployments that have not run
    migrations can still boot. In that scenario a warning is logged and an empty
    cache is returned.
    """

    global _SETTINGS_CACHE
    try:
        rows: Iterable[AppSetting] = AppSetting.query.order_by(
            AppSetting.key.asc()
        ).all()
    except (OperationalError, ProgrammingError) as exc:
        LOGGER.warning("Skipping settings cache refresh: %s", exc)
        _SETTINGS_CACHE = {}
        return {}

    cache: Dict[str, SettingRecord] = {}
    for row in rows:
        normalized = _normalize_key(row.key)
        cache[normalized] = _snapshot(row)

    _SETTINGS_CACHE = cache
    return dict(_SETTINGS_CACHE)


def apply_settings(
    app: Flask, settings: Optional[Mapping[str, SettingRecord]] = None
) -> Dict[str, Optional[SettingValue]]:
    """Apply ``settings`` to ``app.config`` and return the applied overrides."""

    if settings is None:
        settings = get_settings_cache()

    applied: Dict[str, Optional[SettingValue]] = {}
    new_keys: Set[str] = set()
    for normalized_key, record in settings.items():
        config_key = normalized_key.upper()
        new_keys.add(config_key)
        parsed = record.parsed_value
        if config_key not in _BASELINE_CONFIG:
            _BASELINE_CONFIG[config_key] = app.config.get(config_key, _MISSING)
        if parsed is None:
            baseline = _BASELINE_CONFIG.get(config_key, _MISSING)
            if baseline is _MISSING:
                app.config.pop(config_key, None)
            else:
                app.config[config_key] = baseline
            continue
        app.config[config_key] = parsed
        applied[config_key] = parsed

    removed = _APPLIED_CONFIG_KEYS - new_keys
    for config_key in removed:
        baseline = _BASELINE_CONFIG.get(config_key, _MISSING)
        if baseline is _MISSING:
            app.config.pop(config_key, None)
        else:
            app.config[config_key] = baseline

    _APPLIED_CONFIG_KEYS.clear()
    _APPLIED_CONFIG_KEYS.update(new_keys)
    return applied


def reload_overrides(app: Flask) -> Dict[str, SettingRecord]:
    """Refresh cached overrides and immediately apply them to ``app``."""

    settings = refresh_settings_cache()
    apply_settings(app, settings)
    return settings


def load_mail_settings() -> MailSettings:
    """Return SMTP overrides saved through the admin interface."""

    cache = get_settings_cache()
    raw: Dict[str, Optional[str]] = {}
    for key in _MAIL_SETTING_KEYS:
        record = cache.get(key)
        raw[key] = record.raw_value if record else None
    return MailSettings(
        server=_clean_value(raw.get("mail_server")),
        port=_parse_int(raw.get("mail_port")),
        use_tls=_parse_bool(raw.get("mail_use_tls")),
        use_ssl=_parse_bool(raw.get("mail_use_ssl")),
        username=_clean_value(raw.get("mail_username")),
        password=_clean_value(raw.get("mail_password")),
    )


def set_setting(key: str, value: Optional[str], *, is_secret: bool = False) -> None:
    """Persist ``value`` for ``key`` in the :class:`AppSetting` table."""

    normalized_key = _normalize_key(key)
    cleaned_value = _clean_value(value)
    setting = AppSetting.query.filter_by(key=normalized_key).one_or_none()

    if cleaned_value is None:
        if setting is not None:
            db.session.delete(setting)
        _SETTINGS_CACHE.pop(normalized_key, None)
        return

    if setting is None:
        setting = AppSetting(
            key=normalized_key, value=cleaned_value, is_secret=is_secret
        )
        db.session.add(setting)
    else:
        setting.value = cleaned_value
        setting.is_secret = is_secret

    db.session.flush()

    _SETTINGS_CACHE[normalized_key] = _snapshot(setting)


def delete_setting(key: str) -> None:
    """Remove ``key`` from the ``app_settings`` table."""

    set_setting(key, None)


__all__ = [
    "MailSettings",
    "SettingRecord",
    "apply_settings",
    "delete_setting",
    "get_settings_cache",
    "load_mail_settings",
    "refresh_settings_cache",
    "reload_overrides",
    "set_setting",
]
