"""Blueprint for serving branding assets stored on disk.

The mounted branding assets are available via both ``/branding_logos`` and the
preferred public route ``/branding_assets`` for backward compatibility.
"""

from __future__ import annotations

from flask import Blueprint
from flask.typing import ResponseReturnValue

from app.services.branding import brand_logo_mount_response, brand_logo_response

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


@branding_bp.get("/branding_logos/<path:filename>")
@branding_bp.get("/branding_assets/<path:filename>")
def logo_mount_file(filename: str) -> ResponseReturnValue:
    """Return the requested branding logo from the mounted GCS bucket.

    Args:
        filename: URL path for the requested logo relative to the mount.

    Returns:
        ResponseReturnValue: Flask response streaming the requested file.

    External dependencies:
        * :func:`app.services.branding.brand_logo_mount_response` to read the
          file from the mounted GCS bucket path for both the legacy
          ``/branding_logos`` and preferred ``/branding_assets`` routes.
    """

    return brand_logo_mount_response(filename)
