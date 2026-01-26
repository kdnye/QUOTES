"""Helpers for Google Cloud setup actions."""

from __future__ import annotations

import logging
import os
from typing import Mapping, Tuple

import requests
from requests import Response
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

_METADATA_BASE_URL = "http://metadata.google.internal/computeMetadata/v1"
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}
_METADATA_TIMEOUT_SECONDS = 2.0


def _fetch_metadata_value(path: str) -> str | None:
    """Fetch a metadata value from the Google metadata server.

    Args:
        path: Metadata path suffix (for example, ``project/project-id``).

    Returns:
        The metadata value string if available, otherwise ``None``.

    External dependencies:
        * Calls :func:`requests.get` against the Google metadata server.
    """

    url = f"{_METADATA_BASE_URL}/{path.lstrip('/')}"
    try:
        response: Response = requests.get(
            url, headers=_METADATA_HEADERS, timeout=_METADATA_TIMEOUT_SECONDS
        )
    except RequestException as exc:
        logger.warning("Metadata request failed for %s: %s", url, exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "Metadata request returned status %s for %s",
            response.status_code,
            url,
        )
        return None

    value = response.text.strip()
    if not value:
        logger.warning("Metadata request returned empty value for %s", url)
        return None

    return value


def get_project_details() -> Tuple[str | None, str | None]:
    """Return the current GCP project ID and region.

    The function attempts to read ``PROJECT_ID`` and ``REGION`` from the Google
    metadata server (for Cloud Run). If the metadata server is unavailable or
    returns incomplete data, the values fall back to ``os.environ``.

    Returns:
        A tuple of ``(project_id, region)`` values. Each entry may be ``None`` if
        it cannot be resolved.

    External dependencies:
        * Calls :func:`requests.get` via :func:`_fetch_metadata_value`.
        * Reads ``os.environ`` for ``PROJECT_ID`` and ``REGION``.
    """

    project_id = _fetch_metadata_value("project/project-id")
    region = _fetch_metadata_value("instance/region")

    if region and region.startswith("projects/"):
        region = region.split("/")[-1]

    project_id = project_id or os.environ.get("PROJECT_ID")
    region = region or os.environ.get("REGION")

    if not project_id:
        logger.error("PROJECT_ID is not set in metadata or environment")
    if not region:
        logger.error("REGION is not set in metadata or environment")

    return project_id, region


def _parse_secret_version_name(version_name: str) -> Tuple[str, str]:
    """Split a secret version resource name into secret and version parts.

    Args:
        version_name: Full secret version resource name, for example
            ``projects/{project}/secrets/{secret}/versions/{version}``.

    Returns:
        A tuple of ``(secret_name, version)`` where ``secret_name`` is the
        resource path without the ``/versions`` suffix.

    Raises:
        ValueError: If ``version_name`` is not a valid secret version resource.
    """

    segments = version_name.split("/")
    if len(segments) < 6 or segments[-2] != "versions":
        raise ValueError(
            "Secret version name must look like "
            "projects/{project}/secrets/{secret}/versions/{version}."
        )
    secret_name = "/".join(segments[:-2])
    version = segments[-1]
    return secret_name, version


def upsert_secret(secret_id: str, value: str) -> str:
    """Create a secret if needed, then add a new secret version.

    Args:
        secret_id: Secret identifier to create or update.
        value: Secret payload to store as a new version.

    Returns:
        The resource name of the created secret version.

    Raises:
        RuntimeError: If the project ID cannot be resolved.
        ValueError: If ``secret_id`` or ``value`` is empty.

    External dependencies:
        * Calls ``google.cloud.secretmanager.SecretManagerServiceClient``.
        * Calls ``SecretManagerServiceClient.get_secret``.
        * Calls ``SecretManagerServiceClient.create_secret``.
        * Calls ``SecretManagerServiceClient.add_secret_version``.
    """

    if not secret_id:
        raise ValueError("secret_id must be provided")
    if value is None:
        raise ValueError("value must be provided")

    project_id, _ = get_project_details()
    if not project_id:
        raise RuntimeError("PROJECT_ID is required to upsert secrets")

    try:
        from google.api_core.exceptions import GoogleAPICallError, NotFound
        from google.cloud import secretmanager
    except ImportError:
        logger.exception("Secret Manager client library is unavailable")
        raise

    client = secretmanager.SecretManagerServiceClient()
    secret_name = (
        client.secret_path(project_id, secret_id)
        if hasattr(client, "secret_path")
        else f"projects/{project_id}/secrets/{secret_id}"
    )

    try:
        client.get_secret(request={"name": secret_name})
    except NotFound:
        logger.info("Secret %s not found; creating it", secret_id)
        try:
            client.create_secret(
                request={
                    "parent": f"projects/{project_id}",
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except GoogleAPICallError:
            logger.exception("Failed to create secret %s", secret_id)
            raise
    except GoogleAPICallError:
        logger.exception("Failed to fetch secret %s", secret_id)
        raise

    try:
        response = client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": value.encode("utf-8")},
            }
        )
    except GoogleAPICallError:
        logger.exception("Failed to add secret version for %s", secret_id)
        raise

    return response.name


def update_cloud_run_service(env_vars_map: Mapping[str, str]) -> None:
    """Update Cloud Run service environment variables to new secret versions.

    Args:
        env_vars_map: Mapping of environment variable names to secret version
            resource names.

    Returns:
        ``None``. The service is updated in place.

    Raises:
        RuntimeError: If required environment details are missing.
        ValueError: If any secret version resource name is invalid.

    External dependencies:
        * Calls ``google.cloud.run_v2.ServicesClient.get_service``.
        * Calls ``google.cloud.run_v2.ServicesClient.update_service``.
        * Reads ``os.environ`` for ``K_SERVICE``.
    """

    if not env_vars_map:
        logger.info("No environment variables provided for Cloud Run update")
        return

    project_id, region = get_project_details()
    if not project_id or not region:
        raise RuntimeError("PROJECT_ID and REGION are required for Cloud Run")

    service_name = os.environ.get("K_SERVICE")
    if not service_name:
        raise RuntimeError("K_SERVICE is required to update Cloud Run service")

    try:
        from google.api_core.exceptions import GoogleAPICallError
        from google.cloud import run_v2
        from google.protobuf.field_mask_pb2 import FieldMask
    except ImportError:
        logger.exception("Cloud Run client library is unavailable")
        raise

    client = run_v2.ServicesClient()
    service_path = (
        client.service_path(project_id, region, service_name)
        if hasattr(client, "service_path")
        else f"projects/{project_id}/locations/{region}/services/{service_name}"
    )

    try:
        service = client.get_service(name=service_path)
    except GoogleAPICallError:
        logger.exception("Failed to fetch Cloud Run service %s", service_path)
        raise

    for container in service.template.containers:
        existing_env = list(container.env) if container.env else []
        env_by_name = {env.name: env for env in existing_env}

        for env_name, version_name in env_vars_map.items():
            secret_name, version = _parse_secret_version_name(version_name)
            env_by_name[env_name] = run_v2.EnvVar(
                name=env_name,
                value_source=run_v2.EnvVarSource(
                    secret_key_ref=run_v2.SecretKeyRef(
                        secret=secret_name,
                        version=version,
                    )
                ),
            )

        container.env = list(env_by_name.values())

    update_mask = FieldMask(paths=["template.containers"])
    try:
        operation = client.update_service(
            request={"service": service, "update_mask": update_mask}
        )
        operation.result()
    except GoogleAPICallError:
        logger.exception("Failed to update Cloud Run service %s", service_path)
        raise
