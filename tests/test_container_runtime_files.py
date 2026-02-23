from __future__ import annotations

from pathlib import Path


DOCKERFILE_PATH = Path("Dockerfile")
GUNICORN_SCRIPT_PATH = Path("scripts/start_gunicorn.sh")
DOCKERIGNORE_PATH = Path(".dockerignore")


def _read_text_file(path: Path) -> str:
    """Return UTF-8 text content for a repository file.

    Inputs:
        path: Path to the file to read from the repository root.

    Outputs:
        The full UTF-8 text content of the file.

    External dependencies:
        * Reads files from disk through :class:`pathlib.Path`.
    """

    return path.read_text(encoding="utf-8")


def test_dockerfile_uses_cloud_run_start_script() -> None:
    """Ensure the Docker image starts via the Cloud Run launcher script.

    Inputs:
        None.

    Outputs:
        None. The test passes when the Dockerfile keeps ``/app`` as the working
        directory and runs ``scripts/start_gunicorn.sh`` through bash.

    External dependencies:
        * Reads ``Dockerfile`` using :func:`_read_text_file`.
    """

    dockerfile_text = _read_text_file(DOCKERFILE_PATH)

    assert "WORKDIR /app" in dockerfile_text
    assert 'CMD ["/bin/bash", "/app/scripts/start_gunicorn.sh"]' in dockerfile_text


def test_start_script_binds_to_cloud_run_port() -> None:
    """Verify Gunicorn binds to ``$PORT`` and keeps Cloud Run friendly defaults.

    Inputs:
        None.

    Outputs:
        None. The test passes when the launcher script defaults to one worker,
        eight threads, and a timeout of zero while binding to ``:$PORT``.

    External dependencies:
        * Reads ``scripts/start_gunicorn.sh`` using :func:`_read_text_file`.
    """

    script_text = _read_text_file(GUNICORN_SCRIPT_PATH)

    assert 'workers=${GUNICORN_WORKERS:-1}' in script_text
    assert 'threads=${GUNICORN_THREADS:-8}' in script_text
    assert 'timeout=${GUNICORN_TIMEOUT:-0}' in script_text
    assert '--bind ":${port}"' in script_text


def test_dockerignore_excludes_local_virtualenvs() -> None:
    """Confirm local virtual environments are excluded from Docker build context.

    Inputs:
        None.

    Outputs:
        None. The test passes when ``venv/`` and ``.venv/`` are listed in
        ``.dockerignore``.

    External dependencies:
        * Reads ``.dockerignore`` using :func:`_read_text_file`.
    """

    dockerignore_text = _read_text_file(DOCKERIGNORE_PATH)

    assert "venv/" in dockerignore_text
    assert ".venv/" in dockerignore_text
