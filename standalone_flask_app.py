"""Thin wrapper to run the unified Flask application."""

from __future__ import annotations

from app import create_app

app = create_app()

if __name__ == "__main__":  # pragma: no cover - manual execution helper
    app.run(debug=True)
