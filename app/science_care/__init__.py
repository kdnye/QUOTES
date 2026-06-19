"""Expose the Science Care blueprint.

Bundles the multi-lab quote page (``/sc/quote``) and the reference-table
maintenance landing page (``/sc/reference``). Routes are split between
``sc_user_required`` (any SC user) and ``sc_admin_required`` (SC admins
or FSI super-admins) — see ``app/policies.py``.
"""

from flask import Blueprint

science_care_bp = Blueprint(
    "science_care", __name__, template_folder="../../templates"
)

from . import routes  # noqa: F401,E402
