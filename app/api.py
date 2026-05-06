"""Blueprint exposing the JSON API endpoints."""

from __future__ import annotations

import json
import secrets
from typing import Any, Dict

from flask import Blueprint, current_app, g, jsonify, request
from flask.typing import ResponseReturnValue
from flask_limiter.util import get_remote_address

from app import limiter
from app.models import User
from app.services import quote as quote_service

api_bp = Blueprint("api", __name__)


def _api_error_response(
    *, error: str, remediation: str, status_code: int
) -> ResponseReturnValue:
    """Return a standardized JSON API error with actionable remediation.

    Args:
        error: Human-readable error summary for API consumers.
        remediation: Concrete next step the caller can take to resolve the error.
        status_code: HTTP status code to return with the error payload.

    Returns:
        ResponseReturnValue: A ``(json_response, status_code)`` tuple suitable
        for Flask view returns.

    External dependencies:
        Calls :func:`flask.jsonify` to build a response object.
    """

    return jsonify({"error": error, "remediation": remediation}), status_code


def _extract_api_token(authorization_header: str | None) -> str | None:
    """Return the API token provided in an Authorization header.

    Args:
        authorization_header: Raw ``Authorization`` header value supplied by the client.

    Returns:
        The token string when present in the header, otherwise ``None``. The
        ``Bearer`` scheme is only accepted when a token follows it.
    """

    if not authorization_header:
        return None

    parts = authorization_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    if len(parts) == 1 and parts[0].lower() != "bearer":
        return parts[0]
    return None


def _api_rate_limit_value() -> str:
    """Return the configured rate limit string for quote API requests.

    Returns:
        The rate limit string configured in
        ``flask.current_app.config['API_QUOTE_RATE_LIMIT']``.

    External dependencies:
        Reads :data:`flask.current_app.config` for configuration.
    """

    value = current_app.config.get("API_QUOTE_RATE_LIMIT", "30 per minute")
    return str(value or "30 per minute")


def _api_rate_limit_key() -> str:
    """Scope API rate limits by caller IP address and API identity.

    Per-user keys are scoped by user id so that IP rotation does not bypass
    per-key limits. The global service token is scoped by IP + token.

    Returns:
        A string key used by Flask-Limiter to bucket this request.

    External dependencies:
        * Reads :data:`flask.g` for ``api_user`` set by
          :func:`_authorize_api_request`.
        * Reads :data:`flask.request.headers` for the ``Authorization`` header.
        * Calls :func:`flask_limiter.util.get_remote_address` for IP fallback.
    """

    remote_addr = request.remote_addr or get_remote_address()
    api_user = getattr(g, "api_user", None)
    if api_user is not None:
        return f"user:{api_user.id}"
    token = _extract_api_token(request.headers.get("Authorization"))
    if token:
        return f"{remote_addr}:{token}"
    return remote_addr


def _authorize_api_request() -> ResponseReturnValue | None:
    """Validate the API authentication header for JSON API requests.

    Accepts either the global ``API_AUTH_TOKEN`` (service-to-service) or a
    per-user key issued via the admin dashboard. Per-user keys must have
    both ``api_approved`` and ``api_enabled`` set on the matching
    :class:`~app.models.User`. When a per-user key is matched the user
    object is stored on :data:`flask.g` as ``g.api_user`` for downstream
    views.

    Returns:
        ``None`` when the request is authorized. Otherwise returns a JSON
        response with the appropriate HTTP status code.

    External dependencies:
        * Reads :data:`flask.current_app.config` for ``API_AUTH_TOKEN``.
        * Reads :data:`flask.request.headers` for the ``Authorization`` header.
        * Calls :func:`secrets.compare_digest` for constant-time comparison.
        * Queries :class:`~app.models.User` for per-user key lookup.
        * Calls :func:`_api_error_response` to build standardized errors.
    """

    authorization_header = request.headers.get("Authorization")
    if not authorization_header:
        return _api_error_response(
            error="Missing Authorization header.",
            remediation=(
                "Provide an Authorization header using 'Bearer <your_api_key>' "
                "and retry the request."
            ),
            status_code=401,
        )

    provided_token = _extract_api_token(authorization_header)
    if not provided_token:
        return _api_error_response(
            error="Invalid Authorization header.",
            remediation=(
                "Use a single token value in the format 'Bearer <your_api_key>' "
                "without extra words."
            ),
            status_code=401,
        )

    # Check global service token first.
    global_token = current_app.config.get("API_AUTH_TOKEN")
    if global_token and secrets.compare_digest(provided_token, global_token):
        g.api_user = None
        return None

    # Fall back to per-user keys.
    api_user = User.query.filter_by(
        api_key=provided_token, api_approved=True, api_enabled=True
    ).first()
    if api_user is not None:
        g.api_user = api_user
        return None

    return _api_error_response(
        error="Invalid API token.",
        remediation=(
            "Verify your API key is correct and that your account has active "
            "API access. Contact your administrator if you need a key issued."
        ),
        status_code=403,
    )


@api_bp.before_request
def _require_api_auth() -> ResponseReturnValue | None:
    """Enforce API authentication before processing JSON API requests.

    Returns:
        ``None`` when authorization succeeds, otherwise a JSON error response.

    External dependencies:
        Delegates to :func:`_authorize_api_request` for validation.
    """

    return _authorize_api_request()


def _serialize_quote(
    quote_obj: Any, metadata: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Return a JSON-serializable payload for a quote object.

    Args:
        quote_obj: Quote-like object returned by :mod:`app.services.quote`.
        metadata: Optional metadata dictionary to include in the response.

    Returns:
        A dictionary containing the public fields of the quote suitable for
        :func:`flask.jsonify`.
    """

    return {
        "quote_id": quote_obj.quote_id,
        "quote_type": quote_obj.quote_type,
        "origin": quote_obj.origin,
        "destination": quote_obj.destination,
        "weight": quote_obj.weight,
        "weight_method": quote_obj.weight_method,
        "actual_weight": quote_obj.actual_weight,
        "dim_weight": quote_obj.dim_weight,
        "pieces": quote_obj.pieces,
        "total": quote_obj.total,
        "metadata": metadata or {},
    }


@api_bp.post("/quote")
@limiter.limit(_api_rate_limit_value, key_func=_api_rate_limit_key, methods=["POST"])
def api_create_quote() -> ResponseReturnValue:
    """Generate a quote from a JSON payload.

    Args:
        None. The request body must include JSON fields that mirror the
        form-based quote creation workflow (shipment details and requester
        information).

    Returns:
        A JSON response containing the generated quote and metadata, with a
        ``201`` status on success.

    External dependencies:
        Calls :func:`app.services.quote.create_quote` to build the quote.
    """

    data = request.get_json() or {}

    quote_type = data.get("quote_type", "Hotshot")
    if quote_type not in {"Hotshot", "Air"}:
        return _api_error_response(
            error="Invalid quote_type",
            remediation="Set quote_type to either 'Hotshot' or 'Air' and retry.",
            status_code=400,
        )

    result = quote_service.create_quote(
        data.get("user_id"),
        data.get("user_email"),
        quote_type,
        data.get("origin"),
        data.get("destination"),
        data.get("weight", 0),
        pieces=data.get("pieces", 1),
        length=data.get("length", 0.0),
        width=data.get("width", 0.0),
        height=data.get("height", 0.0),
        dim_weight=data.get("dim_weight", 0.0),
        accessorials=data.get("accessorials", []),
    )

    if isinstance(result, tuple):
        quote_obj, metadata = result
    else:  # backward compatibility
        quote_obj, metadata = result, {}

    return jsonify(_serialize_quote(quote_obj, metadata)), 201


@api_bp.get("/quote/<quote_id>")
@limiter.limit(_api_rate_limit_value, key_func=_api_rate_limit_key, methods=["GET"])
def api_get_quote(quote_id: str) -> ResponseReturnValue:
    """Return a previously generated quote as JSON.

    Args:
        quote_id: Quote identifier string provided in the request path.

    Returns:
        A JSON response containing the stored quote payload or a ``404`` error
        when the quote is missing.

    External dependencies:
        Calls :func:`app.services.quote.get_quote` to fetch stored quotes.
    """

    quote_obj = quote_service.get_quote(quote_id)
    if quote_obj is None:
        return _api_error_response(
            error="Quote not found",
            remediation=(
                "Confirm the quote_id exists and belongs to this environment, "
                "then retry the lookup."
            ),
            status_code=404,
        )

    metadata: Dict[str, Any] = {}
    try:
        if quote_obj.quote_metadata:
            metadata = json.loads(quote_obj.quote_metadata)
    except Exception:
        metadata = {}

    return jsonify(_serialize_quote(quote_obj, metadata))
