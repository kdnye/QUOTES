"""Admin views for inspecting and exporting quotes."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Final

from flask import Blueprint, Response, render_template
from flask_login import current_user

from app.models import Quote
from app.policies import employee_required, super_admin_required
from app.services.mail import user_has_mail_privileges


admin_quotes_bp = Blueprint("admin_quotes", __name__, template_folder="../templates")


_FORMULA_PREFIXES: Final[tuple[str, ...]] = ("=", "+", "-", "@")


def _escape_for_csv(value: str | None) -> str:
    """Return ``value`` escaped to avoid CSV formula injection.

    Args:
        value: Raw text extracted from a :class:`app.models.Quote` column. ``None``
            is treated as an empty string so the CSV contains a blank cell.

    Returns:
        str: The sanitized value. Text beginning with characters that spreadsheet
        software interprets as formulas (``=``, ``+``, ``-``, ``@``) is prefixed
        with an apostrophe so the content is rendered literally.

    External dependencies:
        * None.
    """

    if value is None:
        return ""

    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _parse_accessorials(quote_metadata: str | None) -> list[dict[str, object]]:
    """Return ``[{"name": str, "amount": float}]`` parsed from a quote's metadata.

    ``Quote.quote_metadata`` is the JSON blob written by
    :func:`app.services.quote.create_quote`: the ``"accessorials"`` key
    holds a ``{display_name: cost}`` dict. Malformed or missing JSON,
    a missing ``accessorials`` key, or a non-dict value all return an
    empty list so the admin table renders an empty cell instead of
    surfacing raw JSON. Amounts that can't be coerced to ``float`` fall
    back to ``0.0`` so a single bad row can't 500 the page.
    """

    if not quote_metadata:
        return []
    try:
        data = json.loads(quote_metadata)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    accessorials = data.get("accessorials")
    if not isinstance(accessorials, dict):
        return []
    out: list[dict[str, object]] = []
    for name, amount in accessorials.items():
        try:
            amount_f = float(amount)
        except (TypeError, ValueError):
            amount_f = 0.0
        out.append({"name": str(name), "amount": amount_f})
    out.sort(key=lambda item: str(item["name"]).lower())
    return out


@admin_quotes_bp.route("/quotes")
@employee_required(approved_only=True)
def quotes_html() -> str:
    """Render a table of all stored quotes for trusted employees.

    The :func:`app.policies.employee_required` decorator restricts access to
    authenticated super administrators and employees with
    ``employee_approved=True``. The listing allows staff to review recent
    quotes and provides quick access to CSV exports and booking email helpers.

    Returns:
        str: Rendered ``admin_quotes.html`` markup containing the quotes table.

    External dependencies:
        * :class:`app.models.Quote` for database access.
        * :func:`services.mail.user_has_mail_privileges` to toggle email links.
    """
    quotes = Quote.query.order_by(Quote.created_at.desc()).all()
    rows = [
        {
            "quote": q,
            "accessorials": _parse_accessorials(q.quote_metadata),
        }
        for q in quotes
    ]
    return render_template(
        "admin_quotes.html",
        rows=rows,
        can_use_email_request=user_has_mail_privileges(current_user),
    )


@admin_quotes_bp.route("/quotes.csv")
@employee_required(approved_only=True)
def quotes_csv() -> Response:
    """Stream all stored quotes as a CSV file for approved employees.

    Returns:
        Response: Streaming CSV download named ``quotes.csv``.

    External dependencies:
        * :class:`app.models.Quote` to load persisted quote data.
    """
    quotes = Quote.query.order_by(Quote.created_at.desc()).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Quote ID",
            "Client Reference",
            "User ID",
            "User Email",
            "Type",
            "Source",
            "Origin",
            "Destination",
            "Request IP",
            "Weight",
            "Method",
            "Zone",
            "Total",
            "Accessorials",
            "Date",
        ]
    )
    for q in quotes:
        accessorials = _parse_accessorials(q.quote_metadata)
        accessorial_summary = "; ".join(
            f"{item['name']}: ${item['amount']:.2f}"
            for item in accessorials
        )
        writer.writerow(
            [
                q.quote_id,
                _escape_for_csv(q.client_reference),
                q.user_id,
                _escape_for_csv(q.user_email),
                _escape_for_csv(q.quote_type),
                _escape_for_csv(q.quote_source),
                _escape_for_csv(q.origin),
                _escape_for_csv(q.destination),
                _escape_for_csv(q.request_ip),
                q.weight,
                _escape_for_csv(q.weight_method),
                _escape_for_csv(q.zone),
                q.total,
                _escape_for_csv(accessorial_summary),
                q.created_at.strftime("%Y-%m-%d %H:%M") if q.created_at else "",
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=quotes.csv"},
    )


@admin_quotes_bp.route("/quotes/<quote_id>/email")
@super_admin_required
def quote_email_request(quote_id: str) -> str:
    """Reuse the standard quote email request view for admins."""
    from app.quotes.routes import email_request_form as _email_request_form

    return _email_request_form(quote_id)
