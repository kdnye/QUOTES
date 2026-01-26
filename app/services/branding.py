"""Helpers for storing and resolving branding logo assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import urlparse

from flask import Flask, abort, current_app, send_from_directory, url_for
from flask.typing import ResponseReturnValue
from google.cloud import storage as gcs_storage
from werkzeug.datastructures import FileStorage

LOGO_SUBDIR = "company_logos"


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
        Normalized storage backend identifier. Defaults to ``"local"`` and
        accepts values like ``"gcs"`` or ``"google_cloud_storage"`` for GCS.
    """

    return (raw_value or "local").strip().lower()


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
class LocalBrandingStorage:
    """Store branding logos on the local filesystem."""

    app: Optional[Flask] = None

    def save_logo(self, file: FileStorage, rate_set: str, ext: str) -> str:
        """Save a logo in the instance ``company_logos`` directory.

        Args:
            file: Uploaded logo file provided by the admin form.
            rate_set: Normalized rate set identifier.
            ext: Lowercased file extension including the leading dot.

        Returns:
            Filename saved in ``app_settings`` for the logo.

        External dependencies:
            * :func:`app.services.branding.get_brand_logo_dir` for storage paths.
            * :meth:`werkzeug.datastructures.FileStorage.save` to persist files.
        """

        target_app = self.app or current_app
        company_dir = get_brand_logo_dir(target_app)
        company_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{rate_set}{ext}"
        save_path = company_dir / filename
        file.stream.seek(0)
        file.save(str(save_path))
        return filename

    def delete_logo(self, rate_set: str, stored_value: Optional[str]) -> None:
        """Remove a stored logo from local storage when present.

        Args:
            rate_set: Normalized rate set identifier (unused by this backend).
            stored_value: Stored filename or URL from ``app_settings``.

        Returns:
            None. Missing files are ignored.

        External dependencies:
            * :func:`app.services.branding.get_brand_logo_dir` for primary files.
            * :func:`app.services.branding._get_legacy_logo_dir` for fallbacks.
        """

        if not stored_value:
            return
        cleaned = stored_value.strip()
        if cleaned.lower().startswith("http"):
            return
        normalized = Path(cleaned).name
        primary_dir = get_brand_logo_dir(self.app or current_app)
        file_path = primary_dir / normalized
        if not file_path.exists():
            file_path = _get_legacy_logo_dir() / normalized
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError as exc:  # pragma: no cover - best-effort cleanup
                current_app.logger.warning("Failed to remove logo file: %s", exc)


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
        RuntimeError: If GCS storage is selected without ``GCS_BUCKET``.

    External dependencies:
        * :data:`flask.current_app` for configuration access.
    """

    target_app = app or current_app
    backend = _normalize_storage_backend(target_app.config.get("BRANDING_STORAGE"))
    if backend in {"gcs", "google", "google_cloud_storage", "googlecloudstorage"}:
        bucket = (target_app.config.get("GCS_BUCKET") or "").strip()
        if not bucket:
            raise RuntimeError("GCS_BUCKET must be set when BRANDING_STORAGE=gcs.")
        prefix = target_app.config.get("GCS_PREFIX")
        client = target_app.config.get("BRANDING_GCS_CLIENT")
        return GCSBrandingStorage(bucket_name=bucket, prefix=prefix, client=client)
    return LocalBrandingStorage(app=target_app)


def get_brand_logo_dir(app: Optional[Flask] = None) -> Path:
    """Return the directory used to store uploaded branding logos.

    Args:
        app: Optional Flask application. When omitted, ``current_app`` is used.

    Returns:
        Path: Absolute path to the instance-scoped logo directory.

    External dependencies:
        * :data:`flask.current_app` for the active Flask instance path.
    """

    target_app = app or current_app
    return Path(target_app.instance_path) / LOGO_SUBDIR


def resolve_brand_logo_url(raw_value: Optional[str]) -> Optional[str]:
    """Return a public URL for a stored company logo.

    Args:
        raw_value: Stored value from ``app_settings``. May be a filename in the
            instance ``company_logos`` directory, a ``gs://`` GCS location, or
            an absolute URL.

    Returns:
        Public URL string for the logo or ``None`` when no logo is configured.

    External dependencies:
        * :func:`flask.url_for` to build branding asset URLs.
    """

    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if cleaned.lower().startswith("gs://"):
        return _gcs_public_url_from_location(cleaned)
    if cleaned.lower().startswith("http"):
        return cleaned
    filename = Path(cleaned).name
    return url_for("branding.logo_file", filename=filename)


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


def _get_legacy_logo_dir() -> Path:
    """Return the legacy theme logo directory path.

    Returns:
        Path: Path to the legacy logo directory inside ``theme/static``.

    External dependencies:
        * :mod:`app.quote.theme` for the theme blueprint static folder path.
    """

    from app.quote import theme as theme_mod

    return Path(theme_mod.bp.static_folder) / LOGO_SUBDIR


def brand_logo_response(filename: str) -> ResponseReturnValue:
    """Serve a stored logo file from the instance logo directory.

    Args:
        filename: File name stored in ``app_settings`` for a logo. Filenames
            with directories are normalized to their basename.

    Returns:
        ResponseReturnValue: A Flask response streaming the file contents.
        Explicitly raises a 404 error when no file exists in the primary or
        legacy logo directories.

    External dependencies:
        * :func:`flask.abort` to raise a 404 when no logo exists.
        * :func:`flask.send_from_directory` for safe file serving.
        * :func:`app.services.branding.get_brand_logo_dir` for instance storage.
        * :func:`app.services.branding._get_legacy_logo_dir` for legacy fallback.
    """

    normalized = Path(filename).name
    primary_dir = get_brand_logo_dir()
    if (primary_dir / normalized).exists():
        return send_from_directory(primary_dir, normalized)

    legacy_dir = _get_legacy_logo_dir()
    if (legacy_dir / normalized).exists():
        return send_from_directory(legacy_dir, normalized)

    abort(404)
