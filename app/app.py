"""Gunicorn entrypoint exposing the Flask application factory."""

from __future__ import annotations

from app import create_app

__all__ = ["create_app"]
