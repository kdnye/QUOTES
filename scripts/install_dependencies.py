"""Utility helpers to install project dependencies for CI and container builds.

The application relies on packages such as Flask, SQLAlchemy, and Alembic.
This module centralizes installation so automated environments always install
``requirements.txt`` before invoking the test suite, preventing
``ModuleNotFoundError`` failures for skipped dependencies.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


def run_pip(pip_arguments: Sequence[str], *, use_cache: bool) -> None:
    """Execute ``python -m pip`` with the provided arguments.

    Args:
        pip_arguments: Additional command-line arguments appended to
            ``python -m pip`` (for example ``("install", "-r", "requirements.txt")``).
        use_cache: When ``False`` the command injects ``--no-cache-dir`` to avoid
            storing downloaded wheels inside ephemeral build environments.

    Raises:
        subprocess.CalledProcessError: Propagated if ``subprocess.run`` reports
            a failure when executing the pip command.

    This helper wraps :func:`subprocess.run` so callers execute pip with the
    interpreter located at :data:`sys.executable`, ensuring installation occurs
    in the active environment.
    """

    command = [sys.executable, "-m", "pip", *pip_arguments]
    if not use_cache and "install" in pip_arguments:
        install_index = command.index("install") + 1
        command.insert(install_index, "--no-cache-dir")
    subprocess.run(command, check=True)


def install_from_requirements(requirements: Path, *, use_cache: bool = True) -> None:
    """Install packages defined in ``requirements``.

    Args:
        requirements: Path to the requirements file to install.
        use_cache: Indicates whether pip's download cache should be used.

    Raises:
        FileNotFoundError: If the requirements file does not exist.
        subprocess.CalledProcessError: Raised if either pip command fails.

    The function first upgrades pip and then installs the packages listed in
    the requirements file by delegating to :func:`run_pip`.
    """

    if not requirements.exists():
        raise FileNotFoundError(f"Requirements file not found: {requirements}")

    run_pip(("install", "--upgrade", "pip"), use_cache=use_cache)
    run_pip(("install", "-r", str(requirements)), use_cache=use_cache)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the installer.

    Args:
        argv: Optional list of command-line arguments, primarily for testing.

    Returns:
        A populated :class:`argparse.Namespace` with parsed options.

    This helper delegates to :func:`argparse.ArgumentParser.parse_args` to
    interpret the script parameters.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Install the dependencies listed in a requirements file so CI and "
            "Docker builds have the correct environment before tests run."
        )
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("requirements.txt"),
        help="Path to the requirements file to install (defaults to requirements.txt).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable pip's download cache (useful for container builds).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Entrypoint for command-line execution.

    Args:
        argv: Optional list of command-line arguments for invocation.

    Raises:
        FileNotFoundError: If the requested requirements file is missing.
        subprocess.CalledProcessError: Propagated from :func:`install_from_requirements`.

    The function resolves the requirements path relative to the current working
    directory and delegates installation to :func:`install_from_requirements`.
    """

    args = parse_args(argv)
    requirements_path = args.requirements.resolve()
    install_from_requirements(requirements_path, use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
