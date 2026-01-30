from __future__ import annotations

import importlib
import shutil
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
from app.services.branding import (
    LOGO_SUBDIR,
    _get_legacy_logo_dir,
    resolve_brand_logo_url,
)
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


@pytest.fixture()
def app(tmp_path: Path) -> Flask:
    """Create a Flask app wired to a temporary SQLite database.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        A configured Flask application for tests.
    """

    TestConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
    logo_mount = tmp_path / "logos"
    logo_mount.mkdir()
    TestConfig.BRANDING_LOGO_MOUNT_PATH = str(logo_mount)
    app = create_app(TestConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def test_default_branding_logo_mount_path_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the branding logo mount path default is empty when unset.

    Args:
        monkeypatch: Pytest fixture for mutating environment variables.

    Returns:
        None. Assertions validate the default configuration value.

    External dependencies:
        * Reloads the :mod:`config` module via :func:`importlib.reload`.
    """

    monkeypatch.delenv("BRANDING_LOGO_MOUNT_PATH", raising=False)
    import config as config_module

    reloaded_config = importlib.reload(config_module)

    assert reloaded_config.Config.BRANDING_LOGO_MOUNT_PATH == ""


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


def _ensure_setup_user(app: Flask) -> None:
    """Ensure the app has a user record to bypass setup redirects.

    Args:
        app: Flask application needing a user record.

    Returns:
        None. The helper creates a user when the database is empty.

    External dependencies:
        * Calls :func:`tests.test_branding._create_super_admin` to insert a user.
        * Reads :class:`app.models.User` via ``User.query.count``.
    """

    with app.app_context():
        if User.query.count() == 0:
            _create_super_admin()


def test_theme_static_folder_prefers_repo_root(tmp_path: Path) -> None:
    """Ensure the theme static folder prefers repository-root assets.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        None. Assertions validate the static folder selection behavior.

    External dependencies:
        * Calls :func:`app.quote.theme._resolve_theme_static_folder` to select
          the theme static folder based on candidate paths.
    """

    from app.quote import theme as theme_mod

    repo_root = tmp_path / "repo"
    app_root = repo_root / "app"
    theme_file = app_root / "quote" / "theme.py"
    (repo_root / "theme" / "static").mkdir(parents=True)
    (app_root / "theme" / "static").mkdir(parents=True)

    selected = theme_mod._resolve_theme_static_folder(theme_file)
    assert selected == repo_root / "theme" / "static"


def test_theme_static_folder_falls_back_to_app_root(tmp_path: Path) -> None:
    """Ensure the theme static folder falls back to the app root path.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        None. Assertions validate the fallback selection behavior.

    External dependencies:
        * Calls :func:`app.quote.theme._resolve_theme_static_folder` to select
          the theme static folder based on candidate paths.
    """

    from app.quote import theme as theme_mod

    repo_root = tmp_path / "repo"
    app_root = repo_root / "app"
    theme_file = app_root / "quote" / "theme.py"
    (app_root / "theme" / "static").mkdir(parents=True)

    selected = theme_mod._resolve_theme_static_folder(theme_file)
    assert selected == app_root / "theme" / "static"


def test_get_legacy_logo_dir_uses_theme_static_folder(tmp_path: Path) -> None:
    """Ensure legacy logo paths derive from the theme static folder.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        None. Assertions confirm legacy logo directory resolution.

    External dependencies:
        * Reads :data:`app.quote.theme.bp` for the static folder value.
        * Calls :func:`app.services.branding._get_legacy_logo_dir` to confirm
          the derived legacy logo directory.
    """

    from app.quote import theme as theme_mod

    custom_static = tmp_path / "theme" / "static"
    custom_static.mkdir(parents=True)
    original_static = theme_mod.bp.static_folder
    try:
        theme_mod.bp.static_folder = str(custom_static)
        assert _get_legacy_logo_dir() == custom_static / LOGO_SUBDIR
    finally:
        theme_mod.bp.static_folder = original_static


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
def test_logo_form_rejects_invalid_gcs_location(app: Flask, location: str) -> None:
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


def test_resolve_brand_logo_url_supports_gcs_locations(app: Flask) -> None:
    """Confirm GCS locations are converted to mounted branding URLs."""

    with app.app_context():
        url = resolve_brand_logo_url("gs://bucket/path/logo.png")

    assert url == "/branding_assets/path/logo.png"


def test_resolve_brand_logo_url_uses_logos_mount(tmp_path: Path) -> None:
    """Ensure /logos mount config maps to branding asset URLs.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        None. Assertions confirm the branding URL is resolved via the mount.

    External dependencies:
        * Creates a Flask app via :func:`app.create_app`.
        * Calls :func:`app.services.branding.resolve_brand_logo_url` to compute
          the branding logo URL.
    """

    mount_path = Path("/logos")
    created_mount = False
    if not mount_path.exists():
        mount_path.mkdir(parents=True)
        created_mount = True

    class LogosMountConfig(TestConfig):
        """Configuration overrides that enforce a /logos mount path."""

        BRANDING_LOGO_MOUNT_PATH = "/logos"

    LogosMountConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(LogosMountConfig)

    try:
        with app.app_context():
            url = resolve_brand_logo_url("gs://bucket/path/logo.png")

        assert url == "/branding_assets/path/logo.png"
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()

        if created_mount:
            shutil.rmtree(mount_path, ignore_errors=True)


def test_resolve_brand_logo_url_uses_case_insensitive_mount(
    tmp_path: Path,
) -> None:
    """Ensure mount path detection works when casing differs.

    Args:
        tmp_path: Temporary path injected by pytest.

    Returns:
        None. Assertions confirm the case-insensitive mount is detected.

    External dependencies:
        * Creates a Flask app via :func:`app.create_app`.
        * Calls :func:`app.services.branding.resolve_brand_logo_url` to compute
          the branding logo URL.
    """

    mount_path = tmp_path / "Logos"
    mount_path.mkdir(parents=True, exist_ok=True)
    configured_path = tmp_path / "logos"

    class CaseInsensitiveMountConfig(TestConfig):
        """Configuration overrides that use a different path casing."""

        BRANDING_LOGO_MOUNT_PATH = str(configured_path)

    CaseInsensitiveMountConfig.SQLALCHEMY_DATABASE_URI = (
        f"sqlite:///{tmp_path / 'test.db'}"
    )
    app = create_app(CaseInsensitiveMountConfig)

    try:
        with app.app_context():
            url = resolve_brand_logo_url("gs://bucket/path/logo.png")

        assert url == "/branding_assets/path/logo.png"
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()


def test_build_brand_logo_url_uses_rate_set_naming(app: Flask) -> None:
    """Confirm rate set logos use the ``<bucket>/<rate_set>.png`` convention."""

    object_location = build_brand_logo_object_location("gs://bucket/path", "ININ")
    assert object_location == "gs://bucket/path/inin.png"

    with app.app_context():
        url = build_brand_logo_url("gs://bucket/path", "ININ")

    assert url == "/branding_assets/path/inin.png"


def test_build_brand_logo_url_accepts_full_object_paths(app: Flask) -> None:
    """Ensure file paths are not duplicated when already provided."""

    object_location = build_brand_logo_object_location(
        "gs://bucket/path/logo.png", "ININ"
    )
    assert object_location == "gs://bucket/path/logo.png"

    with app.app_context():
        url = build_brand_logo_url("gs://bucket/path/logo.png", "ININ")

    assert url == "/branding_assets/path/logo.png"


def test_build_brand_logo_location_returns_none_for_blank_input() -> None:
    """Ensure blank logo locations do not produce object URLs."""

    assert build_brand_logo_object_location("   ", "ININ") is None
    assert build_brand_logo_url("", "ININ") is None


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
    assert logos[DEFAULT_RATE_SET]["url"] == "/branding_assets/path/default.png"
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

    assert populated_context["company_logo_url"] == "/branding_assets/path/default.png"


@pytest.fixture()
def app_without_logo_mount(tmp_path: Path) -> Flask:
    """Create a Flask app without a branding logo mount path configured.

    Args:
        tmp_path: Temporary filesystem path injected by pytest.

    Returns:
        Flask app instance configured without ``BRANDING_LOGO_MOUNT_PATH``.

    External dependencies:
        * Calls :func:`app.create_app` to build the Flask application.
    """

    class MissingMountConfig(TestConfig):
        """Configuration overrides without a logo mount path.

        Attributes:
            BRANDING_LOGO_MOUNT_PATH: Explicitly disabled mount path setting.
        """

        BRANDING_LOGO_MOUNT_PATH = None

    MissingMountConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(MissingMountConfig)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize(
    "path",
    [
        "/branding_logos/{filename}",
        "/branding_assets/{filename}",
    ],
)
def test_branding_mount_returns_404_when_missing(
    app_without_logo_mount: Flask, path: str
) -> None:
    """Ensure mount-backed branding routes return 404 when unconfigured.

    Args:
        app_without_logo_mount: Flask app without a mount path.
        path: Route template under test.

    Returns:
        None. Assertions validate that a missing mount path yields 404.
    """

    client = app_without_logo_mount.test_client()
    _ensure_setup_user(app_without_logo_mount)
    response = client.get(path.format(filename="logo.png"))

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/branding_logos/{filename}",
        "/branding_assets/{filename}",
    ],
)
def test_branding_mount_blocks_path_traversal(app: Flask, path: str) -> None:
    """Ensure mount-backed branding routes reject path traversal attempts.

    Args:
        app: Flask app with a configured logo mount path.
        path: Route template under test.

    Returns:
        None. Assertions validate that traversal attempts are rejected.
    """

    _ensure_setup_user(app)
    client = app.test_client()
    response = client.get(path.format(filename="../secret.txt"))

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/branding_logos/{filename}",
        "/branding_assets/{filename}",
    ],
)
def test_branding_mount_serves_existing_logo(app: Flask, path: str) -> None:
    """Ensure mount-backed branding routes serve existing logo files.

    Args:
        app: Flask app with a configured logo mount path.
        path: Route template under test.

    Returns:
        None. Assertions validate that existing files are served.

    External dependencies:
        * Writes test assets to the filesystem for Flask to serve.
    """

    mount_path = Path(app.config["BRANDING_LOGO_MOUNT_PATH"])
    logo_path = mount_path / "customer" / "logo.png"
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    logo_path.write_bytes(b"logo-content")

    _ensure_setup_user(app)
    client = app.test_client()
    response = client.get(path.format(filename="customer/logo.png"))

    assert response.status_code == 200
    assert response.data == b"logo-content"
