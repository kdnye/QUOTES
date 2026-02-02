"""Helpers for storing and resolving branding logo assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import urlparse

from flask import Flask, current_app
from google.cloud import storage as gcs_storage
from werkzeug.datastructures import FileStorage


class BrandingStorage(Protocol):
    """Describe the interface for branding logo storage backends.

    External dependencies:
        * :class:`werkzeug.datastructures.FileStorage` for the uploaded file.
    """

    def save_logo(self, file: FileStorage, rate_set: str, ext: str) -> str:
        """Persist a branding logo and return the stored setting value.

        Args:
            file: Uploaded logo file provided by the admin form.
            rate_set: Normalized rate set identifier.
            ext: Lowercased file extension including the leading dot.

        Returns:
            str: Value to persist in ``app_settings`` for the brand logo.
        """

    def delete_logo(self, rate_set: str, stored_value: Optional[str]) -> None:
        """Remove a branding logo from storage when present.

        Args:
            rate_set: Normalized rate set identifier.
            stored_value: Value currently stored in ``app_settings`` for the
                logo, if any.

        Returns:
            None. Missing files are ignored.
        """


def _normalize_storage_backend(raw_value: Optional[str]) -> str:
    """Normalize the branding storage backend setting.

    Args:
        raw_value: Raw backend string from configuration or environment.

    Returns:
        Normalized storage backend identifier. Defaults to ``"disabled"`` and
        accepts values like ``"gcs"`` or ``"google_cloud_storage"`` for GCS.
    """

    if not raw_value:
        return "disabled"

    normalized = raw_value.strip().lower()
    if normalized in {"none", "disabled", "off"}:
        return "disabled"
    return normalized


def is_branding_enabled(raw_value: Optional[str]) -> bool:
    """Return ``True`` when branding storage is enabled.

    Args:
        raw_value: Raw branding storage setting from configuration.

    Returns:
        bool: ``True`` when branding is enabled for a supported backend,
        otherwise ``False``.

    External dependencies:
        * Calls :func:`_normalize_storage_backend` to normalize the setting.
    """

    backend = _normalize_storage_backend(raw_value)
    return backend in {"gcs", "google", "google_cloud_storage", "googlecloudstorage"}


def _normalize_gcs_prefix(prefix: Optional[str]) -> Optional[str]:
    """Return a normalized GCS object prefix or ``None`` when absent.

    Args:
        prefix: Optional prefix configured for the bucket.

    Returns:
        Cleaned prefix without surrounding slashes, or ``None`` when empty.
    """

    if not prefix:
        return None
    cleaned = prefix.strip().strip("/")
    return cleaned or None

@dataclass
class GCSBrandingStorage:
    """Store branding logos in Google Cloud Storage."""

    bucket_name: str
    prefix: Optional[str] = None
    client: Optional[gcs_storage.Client] = None

    def __post_init__(self) -> None:
        """Normalize the prefix and resolve a GCS client.

        External dependencies:
            * :class:`google.cloud.storage.Client` for GCS operations.
        """

        self.prefix = _normalize_gcs_prefix(self.prefix)
        if self.client is None:
            self.client = gcs_storage.Client()

    def save_logo(self, file: FileStorage, rate_set: str, ext: str) -> str:
        """Upload a logo to GCS and return its public URL.

        Args:
            file: Uploaded logo file provided by the admin form.
            rate_set: Normalized rate set identifier.
            ext: Lowercased file extension including the leading dot.

        Returns:
            Public URL for the uploaded object.

        External dependencies:
            * :class:`google.cloud.storage.Bucket` for object storage.
            * :meth:`google.cloud.storage.Blob.upload_from_file` to upload data.
        """

        filename = f"{rate_set}{ext}"
        object_name = self._object_name(filename)
        bucket = self._bucket()
        blob = bucket.blob(object_name)
        file.stream.seek(0)
        content_type = file.mimetype or None
        blob.upload_from_file(file.stream, content_type=content_type)
        return blob.public_url

    def delete_logo(self, rate_set: str, stored_value: Optional[str]) -> None:
        """Delete an existing GCS logo object when possible.

        Args:
            rate_set: Normalized rate set identifier (unused by this backend).
            stored_value: Stored public URL (or filename) for the logo.

        Returns:
            None. Missing objects are ignored.

        External dependencies:
            * :meth:`google.cloud.storage.Blob.delete` to remove objects.
        """

        if not stored_value:
            return
        filename = self._filename_from_value(stored_value)
        if not filename:
            return
        object_name = self._object_name(filename)
        blob = self._bucket().blob(object_name)
        try:
            blob.delete()
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            current_app.logger.warning(
                "Failed to remove GCS logo object %s: %s", object_name, exc
            )

    def _bucket(self) -> gcs_storage.Bucket:
        """Return the configured GCS bucket."""

        if not self.client:
            self.client = gcs_storage.Client()
        return self.client.bucket(self.bucket_name)

    def _object_name(self, filename: str) -> str:
        """Return the GCS object name for a given filename."""

        if self.prefix:
            return f"{self.prefix}/{filename}"
        return filename

    def _filename_from_value(self, stored_value: str) -> Optional[str]:
        """Extract the filename from a stored URL or raw value."""

        cleaned = stored_value.strip()
        if not cleaned:
            return None
        if cleaned.lower().startswith("http"):
            parsed = urlparse(cleaned)
            return Path(parsed.path).name or None
        return Path(cleaned).name or None


def get_branding_storage(app: Optional[Flask] = None) -> BrandingStorage:
    """Return the configured branding storage backend.

    Args:
        app: Optional Flask application. When omitted, ``current_app`` is used.

    Returns:
        BrandingStorage implementation configured for the environment.

    Raises:
        RuntimeError: If branding storage is disabled.
        RuntimeError: If GCS storage is selected without ``GCS_BUCKET``.
        RuntimeError: If an unsupported branding storage backend is selected.

    External dependencies:
        * :data:`flask.current_app` for configuration access.
    """

    target_app = app or current_app
    backend = _normalize_storage_backend(target_app.config.get("BRANDING_STORAGE"))
    if backend == "disabled":
        raise RuntimeError("Branding storage is disabled.")
    if backend in {"gcs", "google", "google_cloud_storage", "googlecloudstorage"}:
        bucket = (target_app.config.get("GCS_BUCKET") or "").strip()
        if not bucket:
            raise RuntimeError("GCS_BUCKET must be set when BRANDING_STORAGE=gcs.")
        prefix = target_app.config.get("GCS_PREFIX")
        client = target_app.config.get("BRANDING_GCS_CLIENT")
        return GCSBrandingStorage(bucket_name=bucket, prefix=prefix, client=client)
    raise RuntimeError(
        "Branding storage must use Google Cloud Storage; bucket mounts are not "
        "supported."
    )


def resolve_brand_logo_url(raw_value: Optional[str]) -> Optional[str]:
    """Return a public URL for a stored company logo.

    Args:
        raw_value: Stored value from ``app_settings``. May be a ``gs://`` GCS
            location or an absolute URL.

    Returns:
        Public URL string for the logo or ``None`` when no logo is configured.

    External dependencies:
        * None. Returns only public GCS URLs or already-absolute URLs.
    """

    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if cleaned.lower().startswith("gs://"):
        return _gcs_public_url_from_location(cleaned)
    if cleaned.lower().startswith("http"):
        return cleaned
    return None


def _gcs_public_url_from_location(location: str) -> Optional[str]:
    """Convert a ``gs://`` location into a public HTTPS URL.

    Args:
        location: GCS object location in ``gs://bucket/path`` format.

    Returns:
        Public HTTPS URL for the object, or ``None`` when parsing fails.

    External dependencies:
        * :func:`urllib.parse.urlparse` for parsing the GCS location.
    """

    parsed = urlparse(location)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path:
        return None
    bucket = parsed.netloc
    object_path = parsed.path.lstrip("/")
    if not object_path:
        return None
    return f"https://storage.googleapis.com/{bucket}/{object_path}"
