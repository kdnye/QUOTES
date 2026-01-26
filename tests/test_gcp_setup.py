from __future__ import annotations

import sys
import types
from typing import Any

import pytest
import requests

from app.services import gcp_setup


class FakeResponse:
    """Simple response stub for metadata tests."""

    def __init__(self, status_code: int, text: str) -> None:
        """Store response metadata for assertions.

        Args:
            status_code: HTTP status code to return.
            text: Response body text.
        """

        self.status_code = status_code
        self.text = text


class FakeSecretVersion:
    """Stub secret version response."""

    def __init__(self, name: str) -> None:
        """Store secret version resource name.

        Args:
            name: Secret version resource name.
        """

        self.name = name


class FakeSecretManagerClient:
    """Secret Manager client fake for upsert tests."""

    last_instance: "FakeSecretManagerClient | None" = None

    def __init__(self) -> None:
        """Capture the latest instance for inspection."""

        self.__class__.last_instance = self
        self.created_secret: dict[str, Any] | None = None
        self.added_payload: dict[str, Any] | None = None

    def secret_path(self, project_id: str, secret_id: str) -> str:
        """Return a fully qualified secret path.

        Args:
            project_id: Project identifier.
            secret_id: Secret identifier.

        Returns:
            Fully-qualified secret resource name.
        """

        return f"projects/{project_id}/secrets/{secret_id}"

    def get_secret(self, request: dict[str, Any]) -> None:
        """Simulate a missing secret by raising NotFound.

        Args:
            request: Request payload with secret name.

        Raises:
            NotFound: Always raised to trigger create logic.
        """

        from google.api_core.exceptions import NotFound

        raise NotFound("missing")

    def create_secret(self, request: dict[str, Any]) -> None:
        """Capture create secret request for assertions.

        Args:
            request: Secret creation payload.
        """

        self.created_secret = request

    def add_secret_version(self, request: dict[str, Any]) -> FakeSecretVersion:
        """Capture the payload and return a fake version.

        Args:
            request: Secret version payload.

        Returns:
            Fake secret version response.
        """

        self.added_payload = request
        return FakeSecretVersion(f"{request['parent']}/versions/1")


class FakeEnvVar:
    """Simple EnvVar fake for Cloud Run tests."""

    def __init__(
        self,
        name: str,
        value: str | None = None,
        value_source: "FakeEnvVarSource | None" = None,
    ) -> None:
        """Store env var inputs.

        Args:
            name: Environment variable name.
            value: Literal value for the env var.
            value_source: Secret reference for the env var.
        """

        self.name = name
        self.value = value
        self.value_source = value_source


class FakeSecretKeyRef:
    """Simple SecretKeyRef fake for Cloud Run tests."""

    def __init__(self, secret: str, version: str) -> None:
        """Store secret reference details.

        Args:
            secret: Secret resource name.
            version: Secret version identifier.
        """

        self.secret = secret
        self.version = version


class FakeEnvVarSource:
    """Simple EnvVarSource fake for Cloud Run tests."""

    def __init__(self, secret_key_ref: FakeSecretKeyRef) -> None:
        """Store secret reference.

        Args:
            secret_key_ref: Secret key reference.
        """

        self.secret_key_ref = secret_key_ref


class FakeContainer:
    """Container fake with env list."""

    def __init__(self, env: list[FakeEnvVar] | None = None) -> None:
        """Store environment list.

        Args:
            env: List of environment variable definitions.
        """

        self.env = env


class FakeTemplate:
    """Template fake that holds containers."""

    def __init__(self, containers: list[FakeContainer]) -> None:
        """Store container list.

        Args:
            containers: Containers to update.
        """

        self.containers = containers


class FakeService:
    """Service fake for Cloud Run tests."""

    def __init__(self, template: FakeTemplate) -> None:
        """Store the service template.

        Args:
            template: Revision template.
        """

        self.template = template


class FakeOperation:
    """Operation fake returned by update_service."""

    def result(self) -> None:
        """No-op result to emulate LRO."""

        return None


class FakeServicesClient:
    """Services client fake for Cloud Run tests."""

    last_instance: "FakeServicesClient | None" = None

    def __init__(self) -> None:
        """Initialize with a fake service and capture instance."""

        self.__class__.last_instance = self
        self.service = FakeService(
            FakeTemplate([FakeContainer([FakeEnvVar("EXISTING", value="ok")])])
        )
        self.update_request: dict[str, Any] | None = None
        self.requested_name: str | None = None

    def service_path(self, project_id: str, region: str, service: str) -> str:
        """Return the service resource name.

        Args:
            project_id: Project identifier.
            region: GCP region.
            service: Service name.

        Returns:
            Fully qualified service path.
        """

        return f"projects/{project_id}/locations/{region}/services/{service}"

    def get_service(self, name: str) -> FakeService:
        """Return the fake service instance.

        Args:
            name: Service resource name.

        Returns:
            Fake service instance.
        """

        self.requested_name = name
        return self.service

    def update_service(self, request: dict[str, Any]) -> FakeOperation:
        """Capture the update request.

        Args:
            request: Update service request payload.

        Returns:
            Fake operation instance.
        """

        self.update_request = request
        return FakeOperation()


class FakeFieldMask:
    """Field mask fake for update requests."""

    def __init__(self, paths: list[str]) -> None:
        """Store field mask paths.

        Args:
            paths: Updated paths list.
        """

        self.paths = paths


def _install_fake_secretmanager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake Secret Manager module into sys.modules.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    fake_secretmanager = types.ModuleType("google.cloud.secretmanager")
    fake_secretmanager.SecretManagerServiceClient = FakeSecretManagerClient

    google_module = sys.modules.setdefault("google", types.ModuleType("google"))
    cloud_module = sys.modules.setdefault(
        "google.cloud", types.ModuleType("google.cloud")
    )
    cloud_module.secretmanager = fake_secretmanager
    google_module.cloud = cloud_module

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_secretmanager)


def _install_fake_run_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake Cloud Run module into sys.modules.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    fake_run_v2 = types.ModuleType("google.cloud.run_v2")
    fake_run_v2.ServicesClient = FakeServicesClient
    fake_run_v2.EnvVar = FakeEnvVar
    fake_run_v2.EnvVarSource = FakeEnvVarSource
    fake_run_v2.SecretKeyRef = FakeSecretKeyRef

    google_module = sys.modules.setdefault("google", types.ModuleType("google"))
    cloud_module = sys.modules.setdefault(
        "google.cloud", types.ModuleType("google.cloud")
    )
    cloud_module.run_v2 = fake_run_v2
    google_module.cloud = cloud_module

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.run_v2", fake_run_v2)

    fake_field_mask_module = types.ModuleType("google.protobuf.field_mask_pb2")
    fake_field_mask_module.FieldMask = FakeFieldMask
    monkeypatch.setitem(
        sys.modules, "google.protobuf.field_mask_pb2", fake_field_mask_module
    )


def test_get_project_details_prefers_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use metadata server values when available."""

    metadata_responses = {
        f"{gcp_setup._METADATA_BASE_URL}/project/project-id": FakeResponse(
            200, "metadata-project"
        ),
        f"{gcp_setup._METADATA_BASE_URL}/instance/region": FakeResponse(
            200, "projects/123/regions/us-central1"
        ),
    }

    def fake_get(url: str, headers: dict[str, str], timeout: float) -> FakeResponse:
        assert headers == gcp_setup._METADATA_HEADERS
        assert timeout == gcp_setup._METADATA_TIMEOUT_SECONDS
        return metadata_responses[url]

    monkeypatch.setattr(gcp_setup.requests, "get", fake_get)
    monkeypatch.delenv("PROJECT_ID", raising=False)
    monkeypatch.delenv("REGION", raising=False)

    project_id, region = gcp_setup.get_project_details()

    assert project_id == "metadata-project"
    assert region == "us-central1"


def test_get_project_details_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback to environment variables when metadata fails."""

    def fake_get(*_: Any, **__: Any) -> FakeResponse:
        raise requests.RequestException("metadata down")

    monkeypatch.setattr(gcp_setup.requests, "get", fake_get)
    monkeypatch.setenv("PROJECT_ID", "env-project")
    monkeypatch.setenv("REGION", "us-east1")

    project_id, region = gcp_setup.get_project_details()

    assert project_id == "env-project"
    assert region == "us-east1"


def test_upsert_secret_creates_and_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create the secret when missing and add a version."""

    _install_fake_secretmanager(monkeypatch)
    monkeypatch.setattr(gcp_setup, "get_project_details", lambda: ("proj", "us"))

    version_name = gcp_setup.upsert_secret("api-key", "value")

    assert version_name == "projects/proj/secrets/api-key/versions/1"
    client = FakeSecretManagerClient.last_instance
    assert client is not None
    assert client.created_secret is not None
    assert client.created_secret["secret_id"] == "api-key"
    assert client.added_payload is not None
    assert client.added_payload["payload"]["data"] == b"value"


def test_update_cloud_run_service_updates_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Update the Cloud Run service with secret-backed env vars."""

    _install_fake_run_v2(monkeypatch)
    monkeypatch.setattr(gcp_setup, "get_project_details", lambda: ("proj", "us"))
    monkeypatch.setenv("K_SERVICE", "my-service")

    gcp_setup.update_cloud_run_service(
        {"API_KEY": "projects/proj/secrets/api-key/versions/2"}
    )

    client = FakeServicesClient.last_instance
    assert client is not None
    assert client.requested_name == "projects/proj/locations/us/services/my-service"
    assert client.update_request is not None

    container = client.service.template.containers[0]
    env_by_name = {env.name: env for env in container.env}
    assert "EXISTING" in env_by_name
    assert "API_KEY" in env_by_name
    updated_env = env_by_name["API_KEY"]
    assert updated_env.value_source is not None
    assert updated_env.value_source.secret_key_ref.secret == (
        "projects/proj/secrets/api-key"
    )
    assert updated_env.value_source.secret_key_ref.version == "2"
    assert client.update_request["update_mask"].paths == ["template.containers"]
