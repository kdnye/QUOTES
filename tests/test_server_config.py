"""Tests for server configuration helpers."""

from __future__ import annotations

from server_config import resolve_debug_flag, resolve_port


def test_resolve_port_defaults_when_env_missing(monkeypatch) -> None:
    """Use the default port when PORT is not set."""

    monkeypatch.delenv("PORT", raising=False)

    assert resolve_port() == 5000


def test_resolve_port_uses_env_value(monkeypatch) -> None:
    """Return the port when a valid value is provided."""

    monkeypatch.setenv("PORT", "8080")

    assert resolve_port() == 8080


def test_resolve_port_falls_back_on_invalid(monkeypatch) -> None:
    """Fall back to the default when PORT is not numeric or out of range."""

    monkeypatch.setenv("PORT", "not-a-port")
    assert resolve_port() == 5000

    monkeypatch.setenv("PORT", "70000")
    assert resolve_port() == 5000


def test_resolve_debug_flag_parses_truthy(monkeypatch) -> None:
    """Return True for truthy values of FLASK_DEBUG."""

    monkeypatch.setenv("FLASK_DEBUG", "yes")

    assert resolve_debug_flag() is True


def test_resolve_debug_flag_parses_falsy(monkeypatch) -> None:
    """Return False for falsy or unknown values of FLASK_DEBUG."""

    monkeypatch.setenv("FLASK_DEBUG", "no")
    assert resolve_debug_flag() is False

    monkeypatch.setenv("FLASK_DEBUG", "maybe")
    assert resolve_debug_flag() is False
