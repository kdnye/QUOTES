"""Help center blueprint providing end-user guidance pages."""

from __future__ import annotations

from typing import Final, List, TypedDict

from flask import Blueprint, render_template


class HelpTopic(TypedDict):
    """Structure describing a help topic listed on the help landing page."""

    slug: str
    title: str
    endpoint: str
    summary: str
    details: list[str]


help_bp = Blueprint("help", __name__)
# Flask blueprint responsible for the ``/help`` section.

HELP_TOPICS: Final[List[HelpTopic]] = [
    {
        "slug": "getting-started",
        "title": "Getting Started",
        "endpoint": "help.getting_started",
        "summary": "Create your account and learn the basics of navigating the quote tool.",
        "details": [
            "Visit the registration page to request account access from your Freight Services Inc. administrator.",
            "Confirm your email address and sign in from the login page to access the quoting dashboard.",
            "Review your profile details from the user menu so that automated emails include your preferred signature.",
        ],
    },
    {
        "slug": "quoting",
        "title": "Quoting",
        "endpoint": "help.quoting",
        "summary": "Generate accurate spot quotes with distance calculations and surcharges.",
        "details": [
            "Open the New Quote form and provide origin, destination, and equipment details for the shipment.",
            "Use the distance preview on the map page to verify mileage before finalizing the quote email.",
            "Send the generated quote via email directly from the tool or download a PDF copy for your records.",
        ],
    },
    {
        "slug": "booking",
        "title": "Booking",
        "endpoint": "help.booking",
        "summary": "Finalize shipments once a customer accepts a quote.",
        "details": [
            "Convert an accepted quote into a booking by following your internal dispatch workflow.",
            "Attach any supporting documents, such as proof of insurance or load confirmations, to the booking record.",
            "Coordinate with dispatch to schedule drivers and confirm pickup details with the shipper.",
        ],
    },
    {
        "slug": "account-management",
        "title": "Account Management",
        "endpoint": "help.account_management",
        "summary": "Keep your profile details and notification preferences up to date.",
        "details": [
            "Update your contact information from the profile page so colleagues can reach you quickly.",
            "Reset your password from the login screen if you forget your credentials or suspect unauthorized access.",
            "Contact an administrator to enable multi-factor authentication for additional security where available.",
        ],
    },
]
"""Ordered list of help topics displayed in the sidebar navigation."""


def _render_help_page(active_topic: str | None) -> str:
    """Return the help landing page with the requested topic highlighted.

    Args:
        active_topic: URL-friendly slug for the topic that should be displayed in
            the main content area. ``None`` shows the general overview.

    Returns:
        Rendered HTML string produced by :func:`flask.render_template`.
    """

    selected_topic: HelpTopic | None = next(
        (topic for topic in HELP_TOPICS if topic["slug"] == active_topic),
        None,
    )
    return render_template(
        "help/index.html",
        topics=HELP_TOPICS,
        active_topic=active_topic,
        selected_topic=selected_topic,
    )


@help_bp.get("")
@help_bp.get("/")
def help_index() -> str:
    """Display the help landing page without focusing on a specific topic.

    Args:
        None.

    Returns:
        Rendered HTML string from :func:`_render_help_page` and ultimately
        :func:`flask.render_template`.
    """

    return _render_help_page(active_topic=None)


@help_bp.get("/getting-started")
def getting_started() -> str:
    """Show the "Getting Started" topic content within the help center.

    Args:
        None.

    Returns:
        Rendered HTML string from :func:`_render_help_page` with the
        ``getting-started`` topic highlighted and rendered by
        :func:`flask.render_template`.
    """

    return _render_help_page(active_topic="getting-started")


@help_bp.get("/quoting")
def quoting() -> str:
    """Provide quoting tips and workflow references for users.

    Args:
        None.

    Returns:
        Rendered HTML string from :func:`_render_help_page` with the
        ``quoting`` topic highlighted and rendered by
        :func:`flask.render_template`.
    """

    return _render_help_page(active_topic="quoting")


@help_bp.get("/quote-types")
def quote_types() -> str:
    """Explain the differences between Hotshot and Air quote types.

    Args:
        None.

    Returns:
        Rendered HTML string produced directly from the
        ``help/quote_types.html`` template via :func:`flask.render_template`.
    """

    return render_template("help/quote_types.html")


@help_bp.get("/booking")
def booking() -> str:
    """Outline next steps for booking freight after a quote is accepted.

    Args:
        None.

    Returns:
        Rendered HTML string from :func:`_render_help_page` with the
        ``booking`` topic highlighted and rendered by
        :func:`flask.render_template`.
    """

    return _render_help_page(active_topic="booking")


@help_bp.get("/account-management")
def account_management() -> str:
    """Explain how to update profile information and secure an account.

    Args:
        None.

    Returns:
        Rendered HTML string from :func:`_render_help_page` with the
        ``account-management`` topic highlighted and rendered by
        :func:`flask.render_template`.
    """

    return _render_help_page(active_topic="account-management")


@help_bp.get("/admin")
def admin() -> str:
    """Outline administrator workflows for managing rates and approvals.

    The view renders a standalone administrator guide that covers rate-table
    configuration, accessorial surcharges, and user approval checkpoints. It
    relies on :func:`flask.render_template` to display the
    ``templates/help/admin.html`` document.

    Returns:
        Rendered HTML string for the administrator help page.
    """

    return render_template("help/admin.html")


@help_bp.get("/password-reset")
def password_reset_guide() -> str:
    """Provide a step-by-step walkthrough for password recovery.

    The view surfaces end-user documentation that explains how the
    :func:`app.auth.reset_request` and :func:`app.auth.reset_with_token` views
    work together. It relies on :func:`flask.render_template` to display the
    ``templates/help/password_reset.html`` article that guides users through
    requesting a reset link and setting a new password.

    Args:
        None.

    Returns:
        Rendered HTML string for the password reset help page.
    """

    return render_template("help/password_reset.html")


@help_bp.get("/register")
def account_setup_guide() -> str:
    """Render the detailed account setup walkthrough.

    The view provides long-form documentation for new users who need help
    completing the registration form. It relies on
    :func:`flask.render_template` to display the
    ``templates/help/register.html`` article.

    Args:
        None.

    Returns:
        Rendered HTML string for the account setup help page.
    """

    return render_template("help/register.html")
