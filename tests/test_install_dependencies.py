from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from scripts import install_dependencies


def test_install_requirements_files_runs_pip_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure pip is upgraded once, then each requirements file is installed."""

    prod_requirements = tmp_path / "requirements.txt"
    dev_requirements = tmp_path / "requirements-dev.txt"
    prod_requirements.write_text("", encoding="utf-8")
    dev_requirements.write_text("", encoding="utf-8")

    calls: list[tuple[tuple[str, ...], bool]] = []

    def fake_run_pip(pip_arguments: tuple[str, ...], *, use_cache: bool) -> None:
        calls.append((pip_arguments, use_cache))

    monkeypatch.setattr(install_dependencies, "run_pip", fake_run_pip)

    install_dependencies.install_requirements_files(
        (prod_requirements, dev_requirements), use_cache=True
    )

    assert calls == [
        (("install", "--upgrade", "pip"), True),
        (("install", "-r", str(prod_requirements)), True),
        (("install", "-r", str(dev_requirements)), True),
    ]


def test_install_requirements_files_missing_file_raises(tmp_path: Path) -> None:
    """Require all requirements files to exist before running pip."""

    missing_requirements = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError):
        install_dependencies.install_requirements_files((missing_requirements,))


def test_parse_args_include_dev_sets_flag() -> None:
    """Validate the dev flag wiring for the argument parser."""

    args = install_dependencies.parse_args(
        ["--include-dev", "--dev-requirements", "dev.txt"]
    )

    assert args.include_dev is True
    assert args.dev_requirements == Path("dev.txt")
