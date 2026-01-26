"""Centralise OpenID Connect client setup for the Flask application.

The module exposes a reusable :class:`authlib.integrations.flask_client.OAuth`
registry so blueprints can obtain a fully configured OIDC client during
requests. Configuration is driven entirely by :class:`config.Config` values and
environment variables which keeps sensitive credentials out of source control.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, Union

from authlib.integrations.flask_client import OAuth
from flask import Flask, current_app


oauth: OAuth = OAuth()


def _normalise_scope(scopes: Union[Sequence[str], str]) -> str:
    """Return a space-delimited scope string understood by Authlib."""

    if isinstance(scopes, str):
        return scopes
    return " ".join(scope.strip() for scope in scopes if scope.strip())


def _configuration_complete(config: Dict[str, Any]) -> bool:
    """Return ``True`` when the minimum OIDC settings are present."""

    required_keys = (
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
        "OIDC_REDIRECT_URI",
    )
    return all(config.get(key) for key in required_keys)


def init_oidc_oauth(app: Flask) -> None:
    """Initialise the global Authlib OAuth registry with IdP metadata.

    Args:
        app: Active :class:`~flask.Flask` application instance created by the
            factory in :mod:`app`.

    The helper inspects the app's configuration for the values introduced in
    :class:`config.Config` and registers an ``"oidc"`` client with Authlib. When
    required settings are missing the function logs a concise message and
    returns without raising so deployments can opt-out of OIDC cleanly. Any
    exceptions raised by Authlib during registration are captured and logged to
    aid troubleshooting while keeping the application available.
    """

    app.config.setdefault("OIDC_CLIENT_REGISTERED", False)
    oauth.init_app(app)

    if not _configuration_complete(app.config):
        app.logger.info("Skipping OIDC registration; configuration incomplete.")
        return

    issuer = str(app.config["OIDC_ISSUER"]).rstrip("/")
    metadata_url = f"{issuer}/.well-known/openid-configuration"
    scope_value = _normalise_scope(app.config.get("OIDC_SCOPES", ()))
    client_kwargs: Dict[str, Any] = {
        "scope": scope_value or "openid",
        "code_challenge_method": "S256",
    }

    try:
        oauth.register(
            name="oidc",
            client_id=app.config["OIDC_CLIENT_ID"],
            client_secret=app.config["OIDC_CLIENT_SECRET"],
            server_metadata_url=metadata_url,
            client_kwargs=client_kwargs,
        )
    except Exception:  # pragma: no cover - handled deterministically in tests
        logging.getLogger("quote_tool.oidc").exception("Failed to register OIDC client")
        app.config["OIDC_CLIENT_REGISTERED"] = False
    else:
        app.config["OIDC_CLIENT_REGISTERED"] = True


def is_oidc_configured(app: Optional[Flask] = None) -> bool:
    """Return ``True`` when the OIDC client is ready for use."""

    target_app = app or current_app
    try:
        config = target_app.config  # type: ignore[assignment]
    except RuntimeError:
        return False

    return bool(
        _configuration_complete(config) and config.get("OIDC_CLIENT_REGISTERED")
    )


def get_oidc_client() -> Optional[Any]:
    """Return the registered Authlib remote application for OIDC flows."""

    if not is_oidc_configured():
        return None

    try:
        return oauth.create_client("oidc")
    except Exception:  # pragma: no cover - defensive guard
        logging.getLogger("quote_tool.oidc").exception("Failed to create OIDC client")
        return None
