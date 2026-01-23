"""Expose the quotes blueprint used to group quote-related views.

The blueprint bundles all quote creation and management routes so they can be
registered with the main Flask application while keeping their templates in the
shared ``app/templates`` directory.
"""

from flask import Blueprint

quotes_bp = Blueprint("quotes", __name__, template_folder="../templates")

from . import routes  # noqa: F401,E402
