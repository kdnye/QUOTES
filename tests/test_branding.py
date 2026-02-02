from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask
from flask.signals import template_rendered
from flask_login import login_user

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app import create_app
from app.admin import LogoUploadForm, branding
from app.models import User, db
from app.services.branding import get_branding_storage, resolve_brand_logo_url
from app.services.branding_locations import (
    build_brand_logo_object_location,
    build_brand_logo_url,
    get_brand_logo_location,
    upsert_brand_logo_location,
)
from app.services.rate_sets import DEFAULT_RATE_SET


class TestConfig:
    """Configuration overrides for branding form tests."""

    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    BRANDING_STORAGE = "gcs"


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


def _create_super_admin(rate_set: str = DEFAULT_RATE_SET) -> User:
    """Create and persist a super admin user for authentication tests.

    Args:
        rate_set: Rate set identifier assigned to the user account.

    Returns:
        Persisted :class:`app.models.User` instance.

    External dependencies:
        * Writes through :data:`app.models.db.session`.
    """

    user = User(
        email="admin@example.com",
        password_hash="unused",
        role="super_admin",
        employee_approved=True,
        rate_set=rate_set,
    )
    user.set_password("StrongPassw0rd!")
    db.session.add(user)
    db.session.commit()
    return user


def _collect_template_context(app: Flask) -> dict[str, object]:
    """Return the merged template context from registered processors.

    Args:
        app: Application instance with template context processors registered.

    Returns:
        Dictionary containing all injected template context values.
    """

    context: dict[str, object] = {}
    for processor in app.template_context_processors[None]:
        context.update(processor())
    return context


@pytest.mark.parametrize(
    "location",
    [
        "not-a-location",
        "gs://bucket",
        "gs://",
        "gs:///path",
        "gs://bucket/",
    ],
)
def test_logo_form_rejects_invalid_gcs_location(
    app: Flask, location: str
) -> None:
    """Ensure the branding form rejects non-GCS locations."""

    with app.test_request_context(
        method="POST",
        data={
            "rate_set": DEFAULT_RATE_SET,
            "gcs_bucket_location": location,
        },
    ):
        form = LogoUploadForm()
        assert form.validate() is False
        assert "gs://bucket/path" in ", ".join(form.gcs_bucket_location.errors)


@pytest.mark.parametrize(
    "location",
    [
        "gs://bucket/path/logo.png",
        "gs://bucket/path",
        "gs://bucket/nested/path/logo.svg",
    ],
)
def test_logo_form_accepts_valid_gcs_location(app: Flask, location: str) -> None:
    """Ensure the branding form accepts valid GCS locations."""

    with app.test_request_context(
        method="POST",
        data={
            "rate_set": DEFAULT_RATE_SET,
            "gcs_bucket_location": location,
        },
    ):
        form = LogoUploadForm()
        assert form.validate() is True


def test_brand_logo_location_persists_per_rate_set(app: Flask) -> None:
    """Ensure GCS locations are persisted per rate set."""

    with app.app_context():
        first_location = "gs://bucket/path/logo.png"
        upsert_brand_logo_location(DEFAULT_RATE_SET, first_location)
        db.session.commit()

        stored = get_brand_logo_location(DEFAULT_RATE_SET)
        assert stored is not None
        assert stored.gcs_bucket_location == first_location

        updated_location = "gs://bucket/path/updated.png"
        upsert_brand_logo_location(DEFAULT_RATE_SET, updated_location)
        db.session.commit()

        updated = get_brand_logo_location(DEFAULT_RATE_SET)
        assert updated is not None
        assert updated.gcs_bucket_location == updated_location


def test_resolve_brand_logo_url_supports_gcs_locations() -> None:
    """Confirm GCS locations are converted to public URLs."""

    url = resolve_brand_logo_url("gs://bucket/path/logo.png")
    assert url == "https://storage.googleapis.com/bucket/path/logo.png"


def test_resolve_brand_logo_url_rejects_local_filenames() -> None:
    """Ensure local filenames do not resolve to public URLs."""

    assert resolve_brand_logo_url("default.png") is None


def test_build_brand_logo_url_uses_rate_set_naming() -> None:
    """Confirm rate set logos use the ``<bucket>/<rate_set>.png`` convention."""

    object_location = build_brand_logo_object_location("gs://bucket/path", "ININ")
    assert object_location == "gs://bucket/path/inin.png"

    url = build_brand_logo_url("gs://bucket/path", "ININ")
    assert url == "https://storage.googleapis.com/bucket/path/inin.png"


def test_build_brand_logo_location_returns_none_for_blank_input() -> None:
    """Ensure blank logo locations do not produce object URLs."""

    assert build_brand_logo_object_location("   ", "ININ") is None
    assert build_brand_logo_url("", "ININ") is None


def test_get_branding_storage_rejects_non_gcs_backend(app: Flask) -> None:
    """Ensure non-GCS branding storage backends are rejected safely.

    Args:
        app: Application instance configured for tests.

    External dependencies:
        * Calls :func:`app.services.branding.get_branding_storage` for selection.
    """

    app.config["BRANDING_STORAGE"] = "local"
    app.config["GCS_BUCKET"] = "branding-bucket"
    with app.app_context(), pytest.raises(RuntimeError, match="bucket mounts"):
        get_branding_storage(app)


def test_get_branding_storage_rejects_disabled_backend(app: Flask) -> None:
    """Ensure disabled branding storage is rejected safely.

    Args:
        app: Application instance configured for tests.

    External dependencies:
        * Calls :func:`app.services.branding.get_branding_storage` for selection.
    """

    app.config["BRANDING_STORAGE"] = "disabled"
    with app.app_context(), pytest.raises(RuntimeError, match="disabled"):
        get_branding_storage(app)


def test_branding_payload_includes_blank_and_populated_logos(app: Flask) -> None:
    """Confirm branding payload renders both populated and blank logo data."""

    captured: list[dict[str, object]] = []

    def _record(
        sender: Flask, template, context: dict[str, object], **extra: object
    ) -> None:
        if template and template.name == "admin_branding.html":
            captured.append(context)

    template_rendered.connect(_record, app)
    try:
        with app.test_request_context("/admin/branding"):
            upsert_brand_logo_location(DEFAULT_RATE_SET, "gs://bucket/path")
            db.session.commit()
            branding.__wrapped__()
    finally:
        template_rendered.disconnect(_record, app)

    assert captured, "Expected branding template context to be captured."
    logos = captured[0]["logos"]
    assert logos[DEFAULT_RATE_SET]["url"] == (
        "https://storage.googleapis.com/bucket/path/default.png"
    )
    assert logos[DEFAULT_RATE_SET]["object_location"] == (
        "gs://bucket/path/default.png"
    )
    assert logos["inin"]["url"] is None
    assert logos["inin"]["object_location"] is None


def test_company_logo_context_blank_and_populated(app: Flask) -> None:
    """Ensure logo context is empty when missing and populated when stored."""

    with app.test_request_context("/"):
        user = _create_super_admin()
        login_user(user)
        empty_context = _collect_template_context(app)
        assert "company_logo_url" not in empty_context

        upsert_brand_logo_location(DEFAULT_RATE_SET, "gs://bucket/path")
        db.session.commit()
        populated_context = _collect_template_context(app)

    assert populated_context["company_logo_url"] == (
        "https://storage.googleapis.com/bucket/path/default.png"
    )


def test_company_logo_context_disabled_returns_empty(app: Flask) -> None:
    """Ensure company logo context stays empty when branding is disabled.

    Args:
        app: Application instance configured for tests.

    External dependencies:
        * Calls :func:`flask_login.login_user` to authenticate the user.
        * Calls :func:`app.services.branding_locations.upsert_brand_logo_location`
          to persist a logo location.
    """

    app.config["BRANDING_STORAGE"] = "disabled"
    with app.test_request_context("/"):
        user = _create_super_admin()
        login_user(user)
        upsert_brand_logo_location(DEFAULT_RATE_SET, "gs://bucket/path")
        db.session.commit()
        context = _collect_template_context(app)

    assert "company_logo_url" not in context
