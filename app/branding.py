"""Blueprint for serving branding assets stored on disk."""

from __future__ import annotations

from flask import Blueprint
from flask.typing import ResponseReturnValue

from app.services.branding import brand_logo_response

branding_bp = Blueprint("branding", __name__)


@branding_bp.get("/branding/logos/<path:filename>")
def logo_file(filename: str) -> ResponseReturnValue:
    """Return the requested branding logo file.

    Args:
        filename: File name stored in ``app_settings`` for a logo.

    Returns:
        ResponseReturnValue: Flask response streaming the requested file.

    External dependencies:
        * :func:`app.services.branding.brand_logo_response` to load the file.
    """

    return brand_logo_response(filename)
