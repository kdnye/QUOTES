from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app
from app.admin import LogoUploadForm
from app.models import db
from app.services.branding import resolve_brand_logo_url
from app.services.rate_sets import DEFAULT_RATE_SET


class TestConfig:
    """Configuration overrides for branding form tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app(tmp_path: Path) -> Flask:
    """Create a Flask app wired to a temporary SQLite database.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        A configured Flask application for tests.
    """

    TestConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(TestConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def test_logo_form_rejects_invalid_gcs_location(app: Flask) -> None:
    """Ensure the branding form rejects non-GCS locations."""

    with app.test_request_context(
        method="POST",
        data={"rate_set": DEFAULT_RATE_SET, "gcs_location": "not-a-location"},
    ):
        form = LogoUploadForm()
        assert form.validate() is False
        assert "gs://bucket/path" in ", ".join(form.gcs_location.errors)


def test_logo_form_accepts_valid_gcs_location(app: Flask) -> None:
    """Ensure the branding form accepts valid GCS locations."""

    with app.test_request_context(
        method="POST",
        data={
            "rate_set": DEFAULT_RATE_SET,
            "gcs_location": "gs://bucket/path/logo.png",
        },
    ):
        form = LogoUploadForm()
        assert form.validate() is True


def test_resolve_brand_logo_url_supports_gcs_locations() -> None:
    """Confirm GCS locations are converted to public URLs."""

    url = resolve_brand_logo_url("gs://bucket/path/logo.png")
    assert url == "https://storage.googleapis.com/bucket/path/logo.png"
