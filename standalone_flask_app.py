"""Thin wrapper to run the unified Flask application."""

from __future__ import annotations

from app.app import create_app
from server_config import resolve_port

app = create_app()

if __name__ == "__main__":  # pragma: no cover - manual execution helper
    app.run(debug=True, port=resolve_port())
