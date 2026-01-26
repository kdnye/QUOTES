"""Tests for application entrypoints."""

from __future__ import annotations


def test_app_module_exports_create_app() -> None:
    """Verify the ``app`` module exposes the Flask application factory.

    Args:
        None.

    Returns:
        None.

    External dependencies:
        * Imports :func:`app.create_app` from ``app/__init__.py``.
    """

    from app import create_app

    assert callable(create_app)
