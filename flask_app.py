"""Main server start-up script.

Imports :func:`app.create_app` and runs the Flask development server.
"""

from __future__ import annotations

import os
from typing import Final

from app import create_app

TRUE_VALUES: Final[set[str]] = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES: Final[set[str]] = {"0", "false", "f", "no", "n", "off"}
DEFAULT_DEBUG: Final[bool] = False


def resolve_debug_flag(env_var: str = "FLASK_DEBUG") -> bool:
    """Return whether Flask should run in debug mode.

    Args:
        env_var: Name of the environment variable that stores the debug flag.

    Returns:
        ``True`` when debugging should be enabled. Falls back to
        :data:`DEFAULT_DEBUG` (``False``) if the variable is unset or contains an
        unrecognised value. Reads the environment via :func:`os.getenv` so
        deployments can toggle the setting without code changes.
    """

    raw_value = os.getenv(env_var)
    if raw_value is None:
        return DEFAULT_DEBUG

    normalized_value = raw_value.strip().lower()
    if normalized_value in TRUE_VALUES:
        return True
    if normalized_value in FALSE_VALUES:
        return False
    return DEFAULT_DEBUG


app = create_app()
app.config["DEBUG"] = resolve_debug_flag()


if __name__ == "__main__":
    app.run(debug=app.debug, host="0.0.0.0", port=5000)
