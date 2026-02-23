from __future__ import annotations

from pathlib import Path


DEPLOY_SCRIPT_PATH = Path("scripts/deploy.sh")


def _read_deploy_script() -> str:
    """Return the deployment script content used by Cloud Run operators.

    Inputs:
        None.

    Outputs:
        The full text of ``scripts/deploy.sh`` as a Python string.

    External dependencies:
        * Reads the script file from disk through :class:`pathlib.Path`.
    """

    return DEPLOY_SCRIPT_PATH.read_text(encoding="utf-8")


def test_deploy_script_rejects_cloud_run_placeholder_image() -> None:
    """Ensure deploy flow blocks known placeholder container images.

    Inputs:
        None.

    Outputs:
        None. The test passes when a placeholder-image guard exists in the
        deployment script.

    External dependencies:
        * Reads ``scripts/deploy.sh`` using :func:`_read_deploy_script`.
    """

    script_text = _read_deploy_script()

    assert "is_placeholder_image" in script_text
    assert "cloudrun/placeholder" in script_text
    assert "the selected image" in script_text


def test_deploy_script_documents_custom_image_example() -> None:
    """Verify deployment error guidance includes an Artifact Registry example.

    Inputs:
        None.

    Outputs:
        None. The test passes when the script includes a clear custom-image
        example to help users recover from placeholder deployments.

    External dependencies:
        * Reads ``scripts/deploy.sh`` using :func:`_read_deploy_script`.
    """

    script_text = _read_deploy_script()

    assert (
        "us-central1-docker.pkg.dev/${PROJECT_ID}/REPO_NAME/quote-tool:latest"
        in script_text
    )
