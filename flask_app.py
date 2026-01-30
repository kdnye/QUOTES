"""Main server start-up script.

Imports :func:`app.create_app` and runs the Flask development server using the
``PORT`` environment variable when available.
"""

from __future__ import annotations

from app import create_app
from server_config import resolve_debug_flag, resolve_port

app = create_app()
app.config["DEBUG"] = resolve_debug_flag()


if __name__ == "__main__":
    app.run(debug=app.debug, host="0.0.0.0", port=resolve_port())
