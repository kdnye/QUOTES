"""Configuration helpers for server entrypoints."""

from __future__ import annotations

import os
from typing import Final

TRUE_VALUES: Final[set[str]] = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES: Final[set[str]] = {"0", "false", "f", "no", "n", "off"}
DEFAULT_DEBUG: Final[bool] = False
DEFAULT_PORT: Final[int] = 5000


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


def resolve_port(env_var: str = "PORT", default_port: int = DEFAULT_PORT) -> int:
    """Return the port that the web server should listen on.

    Args:
        env_var: Name of the environment variable that stores the port.
        default_port: Port to use when the variable is missing or invalid.

    Returns:
        A valid TCP port from ``env_var``. Falls back to ``default_port`` when
        the environment variable is unset or cannot be parsed as a positive
        integer. Calls :func:`os.getenv` to read environment variables.
    """

    raw_value = os.getenv(env_var, "").strip()
    if not raw_value:
        return default_port

    try:
        port = int(raw_value)
    except ValueError:
        return default_port

    if 1 <= port <= 65535:
        return port

    return default_port
