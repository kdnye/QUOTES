"""Routes registered on the quotes blueprint.

Routes:
    ``/new``: Display the quote form and create air or hotshot quotes. The
    endpoint requires an authenticated user and accepts both form and JSON
    submissions.
"""

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from flask import current_app, flash, jsonify, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from . import quotes_bp
from ..models import (
    db,
    Quote,
    Accessorial,
    ZipZone,
    CostZone,
    AirCostZone,
)
from app.quote.logic_hotshot import calculate_hotshot_quote
from app.quote.logic_air import calculate_air_quote
from app.quote.thresholds import check_thresholds, check_air_piece_limit
from app.quote.zip_validation import validate_us_zip
from app.services.mail import send_email, user_has_mail_privileges
from app.services.settings import is_quote_email_smtp_enabled
from app.services.rate_sets import DEFAULT_RATE_SET, normalize_rate_set

logger = logging.getLogger(__name__)


def _get_client_ip() -> str | None:
    """Return the originating client IP address for the current request.

    The helper inspects the ``X-Forwarded-For`` header first to honor client
    addresses forwarded by a trusted proxy (for example, an Nginx instance or
    a Cloud Run HTTPS load balancer). When the header is present, the function
    returns the first comma-separated value because proxies append new
    addresses to the end of the list. If the header is missing or empty, it
    falls back to
    :attr:`flask.Request.remote_addr`, which is populated by Flask's request
    context.

    Returns:
        The detected client IP address as a string, or ``None`` when Flask
        could not determine the address.
    """

    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.remote_addr


@dataclass(frozen=True)
class AccessorialInfo:
    """Cached metadata describing an individual accessorial charge.

    Attributes:
        name: Display name sourced from :class:`app.models.Accessorial`.
        amount: Dollar amount applied when the accessorial is selected.
    """

    name: str
    amount: float


@lru_cache(maxsize=1)
def _accessorial_cache() -> tuple[tuple[str, ...], Mapping[str, AccessorialInfo]]:
    """Return cached accessorial names and lookup information.

    The initial call performs a database query and later calls reuse the
    cached data, avoiding redundant lookups during GET or POST requests to the
    quote form.

    Returns:
        A pair of ``(options, lookup)`` where ``options`` preserves the
        configured display order and ``lookup`` normalizes accessorial names to
        lowercase for case-insensitive matching.
    """

    records = Accessorial.query.order_by(Accessorial.id).all()
    if not records:
        logger.error("Accessorial table is empty.")

    option_names: list[str] = []
    lookup: dict[str, AccessorialInfo] = {}
    for record in records:
        name = record.name
        if not name:
            logger.warning(
                "Skipping accessorial without a name (id=%s).",
                getattr(record, "id", "?"),
            )
            continue
        option_names.append(name)
        lookup[name.lower()] = AccessorialInfo(
            name=name, amount=float(record.amount or 0.0)
        )

    return tuple(option_names), MappingProxyType(lookup)


def _get_accessorial_choices() -> tuple[list[str], Mapping[str, AccessorialInfo]]:
    """Expose cached accessorial options and lookup data.

    Returns:
        A list of accessorial names suitable for rendering in the template
        along with a mapping keyed by lowercase accessorial name for use when
        calculating totals.
    """

    options, lookup = _accessorial_cache()
    return list(options), lookup


def clear_accessorial_cache() -> None:
    """Reset cached accessorial query results.

    Tests and administrative workflows that modify accessorials can call this
    helper to ensure new data is fetched on the next request.
    """

    _accessorial_cache.cache_clear()


@lru_cache(maxsize=1)
def _air_rate_table_cache() -> tuple[str, ...]:
    """Return cached names of missing or empty air rate tables."""

    inspector = inspect(db.engine)
    required_tables: tuple[tuple[type, str], ...] = (
        (ZipZone, "ZipZone"),
        (CostZone, "CostZone"),
        (AirCostZone, "AirCostZone"),
    )
    missing: list[str] = []
    for model, label in required_tables:
        try:
            if not inspector.has_table(model.__tablename__):
                missing.append(label)
                continue
            if db.session.query(model).first() is None:
                missing.append(label)
        except OperationalError:
            missing.append(label)

    return tuple(missing)


def _get_missing_air_rate_tables() -> list[str]:
    """Expose cached information about missing air rate tables."""

    return list(_air_rate_table_cache())


def clear_air_rate_cache() -> None:
    """Reset cached air-rate table inspection results."""

    _air_rate_table_cache.cache_clear()


@quotes_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_quote():
    """Create a new freight quote via form or JSON.

    If :func:`calculate_air_quote` returns an ``error`` key, the message is
    flashed and the quote form is re-rendered without saving a record.
    Warnings are added when quotes exceed predefined limits using
    :func:`quote.thresholds.check_thresholds`.
    """
    accessorial_options, accessorial_map = _get_accessorial_choices()
    maps_api_key = (
        current_app.config.get("GOOGLE_MAPS_API_KEY")
        or os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("MAPS_API_KEY")
    )

    if request.method == "POST":
        data = request.form or request.json or {}
        quote_type = data.get("quote_type", "Air")
        origin = data.get("origin_zip") or data.get("origin", "")
        destination = data.get("dest_zip") or data.get("destination", "")

        errors: list[str] = []
        origin_valid, origin_reason = validate_us_zip(origin, api_key=maps_api_key)
        destination_valid, destination_reason = validate_us_zip(
            destination, api_key=maps_api_key
        )
        if not origin_valid:
            if origin_reason == "invalid_format":
                errors.append("Origin ZIP must include at least 5 digits.")
            else:
                errors.append("Origin ZIP could not be validated with Google Places.")
        if not destination_valid:
            if destination_reason == "invalid_format":
                errors.append("Destination ZIP must include at least 5 digits.")
            else:
                errors.append(
                    "Destination ZIP could not be validated with Google Places."
                )

        weight_actual_raw = data.get("weight_actual")
        if weight_actual_raw in (None, ""):
            errors.append("Actual weight is required and must be a number.")
            weight_actual = 0.0
        else:
            try:
                weight_actual = float(weight_actual_raw)
                if weight_actual < 0:
                    errors.append("Actual weight must be non-negative.")
            except (TypeError, ValueError):
                errors.append("Actual weight is required and must be a number.")
                weight_actual = 0.0

        # Pieces default to 1
        pieces = 1
        pieces_raw = data.get("pieces")
        if pieces_raw not in (None, ""):
            try:
                pieces = int(pieces_raw)
                if pieces < 1:
                    errors.append("Pieces must be at least 1.")
                    pieces = 1
            except (TypeError, ValueError):
                errors.append("Pieces must be a whole number.")

        def _parse_dim(name: str) -> float:
            raw = data.get(name)
            if raw in (None, ""):
                return 0.0
            try:
                val = float(raw)
                if val < 0:
                    errors.append(f"{name.title()} must be non-negative.")
                    return 0.0
                return val
            except (TypeError, ValueError):
                errors.append(f"{name.title()} must be a number.")
                return 0.0

        length = _parse_dim("length")
        width = _parse_dim("width")
        height = _parse_dim("height")

        weight_dim_raw = data.get("weight_dim")
        if weight_dim_raw not in (None, ""):
            try:
                weight_dim = float(weight_dim_raw)
            except (TypeError, ValueError):
                errors.append("Dimensional weight must be a number.")
                weight_dim = 0.0
        else:
            weight_dim = 0.0
            if length and width and height:
                weight_dim = ((length * width * height) / 166.0) * pieces

        if piece_err := check_air_piece_limit(
            quote_type,
            weight_actual,
            pieces,
            weight_dim,
        ):
            errors.append(piece_err)

        if request.is_json:
            accessorials_field = data.get("accessorials") or []
            if isinstance(accessorials_field, str):
                try:
                    accessorials_json = json.loads(accessorials_field)
                except Exception:
                    accessorials_json = []
            else:
                accessorials_json = accessorials_field
        else:
            accessorials_json = request.form.getlist("accessorials")

        if errors:
            if request.is_json:
                return jsonify({"errors": errors}), 400
            return (
                render_template(
                    "new_quote.html",
                    errors=errors,
                    accessorial_options=accessorial_options,
                    quote_type=quote_type,
                    maps_api_key=maps_api_key,
                ),
                400,
            )

        selected: list[str] = []
        if isinstance(accessorials_json, dict):
            selected = list(accessorials_json.keys())
        elif isinstance(accessorials_json, list):
            selected = accessorials_json

        guarantee_selected = any("guarantee" in s.lower() for s in selected)
        fixed_accessorials = [s for s in selected if "guarantee" not in s.lower()]

        accessorial_costs: dict[str, float] = {}
        for acc in fixed_accessorials:
            record = accessorial_map.get(str(acc).strip().lower())
            if record:
                accessorial_costs[record.name] = record.amount

        accessorial_total = sum(accessorial_costs.values())

        billable_weight = max(weight_actual, weight_dim)
        weight_method = (
            "Dimensional"
            if billable_weight == weight_dim and weight_dim > 0
            else "Actual"
        )

        warnings: list[str] = []
        active_rate_set = normalize_rate_set(
            getattr(current_user, "rate_set", DEFAULT_RATE_SET)
        )

        if quote_type.lower() == "air":
            missing_tables = _get_missing_air_rate_tables()
            if missing_tables:
                msg = "Air rate table(s) missing or empty: " + ", ".join(missing_tables)
                warnings.append(msg)
                logger.error(msg)
                result = {"quote_total": 0.0, "beyond_total": 0.0, "error": msg}
            else:
                result = calculate_air_quote(
                    origin,
                    destination,
                    billable_weight,
                    accessorial_total,
                    rate_set=active_rate_set,
                )
                if err := result.get("error"):
                    flash(err, "warning")
                    return (
                        render_template(
                            "new_quote.html",
                            accessorial_options=accessorial_options,
                            quote_type=quote_type,
                            errors=[err],
                            maps_api_key=maps_api_key,
                        ),
                        400,
                    )
        else:
            try:
                result = calculate_hotshot_quote(
                    origin,
                    destination,
                    billable_weight,
                    accessorial_total,
                    rate_set=active_rate_set,
                )
            except ValueError as e:
                warnings.append(str(e))
                result = {"quote_total": 0.0}

        price = result.get("quote_total", 0.0)
        threshold_warning = check_thresholds(quote_type, billable_weight, price)
        exceeds_threshold: bool = bool(threshold_warning)
        if exceeds_threshold:
            warnings.append(threshold_warning)
        if quote_type.lower() == "air" and guarantee_selected:
            # Guarantee covers the linehaul and beyond charges, excluding other accessorials.
            linehaul_with_beyond = price - accessorial_total
            guarantee_cost = linehaul_with_beyond * 0.25
            accessorial_costs["Guarantee"] = guarantee_cost
            accessorial_total += guarantee_cost
            price += guarantee_cost

        metadata = {
            "accessorials": accessorial_costs,
            "accessorial_total": accessorial_total,
            "miles": result.get("miles"),
            "pieces": pieces,
            "details": {
                k: v
                for k, v in result.items()
                if k not in {"quote_total", "miles", "zone"}
            },
        }

        client_ip = _get_client_ip()
        q = Quote(
            quote_type=quote_type.title(),
            origin=origin,
            destination=destination,
            weight=billable_weight,
            weight_method=weight_method,
            actual_weight=weight_actual,
            dim_weight=weight_dim,
            pieces=pieces,
            length=length,
            width=width,
            height=height,
            rate_set=active_rate_set,
            request_ip=client_ip,
            total=price,
            quote_metadata=json.dumps(metadata),
            warnings="\n".join(warnings) if warnings else "",
            user=current_user,
            user_email=current_user.email,
        )
        db.session.add(q)
        db.session.commit()

        if request.is_json:
            return jsonify(
                {
                    "id": q.id,
                    "price": q.total,
                    "warnings": q.warnings,
                    "exceeds_threshold": exceeds_threshold,
                }
            )
        quote_email_smtp_enabled = is_quote_email_smtp_enabled()
        user_can_send_quote_email = user_has_mail_privileges(current_user)
        return render_template(
            "quote_result.html",
            quote=q,
            metadata=metadata,
            exceeds_threshold=exceeds_threshold,
            can_request_booking_email=bool(
                getattr(current_user, "is_authenticated", False)
            ),
            can_send_quote_email=(
                user_can_send_quote_email and quote_email_smtp_enabled
            ),
            quote_email_smtp_enabled=quote_email_smtp_enabled,
            user_can_send_quote_email=user_can_send_quote_email,
        )

    return render_template(
        "new_quote.html",
        accessorial_options=accessorial_options,
        quote_type="Air",
        maps_api_key=maps_api_key,
    )


@quotes_bp.route("/lookup", methods=["GET", "POST"])
@login_required
def lookup_quote():
    """Render a quote lookup form and display a saved quote by public ID.

    Inputs:
        Reads ``quote_id`` from ``request.form`` during POST requests and
        validates the value as a UUID string.

    Outputs:
        Returns ``lookup_quote.html`` for GET requests and for invalid or
        missing quote IDs. Returns ``quote_result.html`` with quote details
        when a matching record is found.

    External dependencies:
        Uses ``Quote.query.filter_by`` to load persisted quotes, calls
        :func:`app.quote.thresholds.check_thresholds` to recompute the
        threshold warning banner, and uses
        :func:`app.services.settings.is_quote_email_smtp_enabled` plus
        :func:`app.services.mail.user_has_mail_privileges` to build email
        permission flags expected by the result template.
    """

    if request.method == "GET":
        return render_template("lookup_quote.html")

    quote_id = request.form.get("quote_id", "").strip()
    try:
        UUID(quote_id)
    except (TypeError, ValueError):
        flash("Please enter a valid Quote ID.", "danger")
        return render_template("lookup_quote.html")

    quote = Quote.query.filter_by(quote_id=quote_id).first()
    if quote is None:
        flash("Quote not found. Please verify the Quote ID and try again.", "danger")
        return render_template("lookup_quote.html")

    try:
        metadata = json.loads(quote.quote_metadata or "{}")
    except json.JSONDecodeError:
        metadata = {}

    threshold_warning = check_thresholds(quote.quote_type, quote.weight, quote.total)
    exceeds_threshold = bool(threshold_warning)

    quote_email_smtp_enabled = is_quote_email_smtp_enabled()
    user_can_send_quote_email = user_has_mail_privileges(current_user)

    return render_template(
        "quote_result.html",
        quote=quote,
        metadata=metadata,
        exceeds_threshold=exceeds_threshold,
        can_request_booking_email=bool(
            getattr(current_user, "is_authenticated", False)
        ),
        can_send_quote_email=(user_can_send_quote_email and quote_email_smtp_enabled),
        quote_email_smtp_enabled=quote_email_smtp_enabled,
        user_can_send_quote_email=user_can_send_quote_email,
    )


def _quote_template_context(
    quote: Quote, metadata: dict[str, object]
) -> dict[str, object]:
    """Build the shared context used to render ``quote_result.html``.

    Args:
        quote: Persisted :class:`app.models.Quote` shown on the result page.
        metadata: Quote metadata dictionary loaded from ``quote.quote_metadata``.

    Returns:
        A context dictionary containing quote details, warning status, and
        permission flags expected by ``templates/quote_result.html``.

    External dependencies:
        * Calls :func:`app.quote.thresholds.check_thresholds` to recompute
          warning banner state.
        * Uses :func:`app.services.settings.is_quote_email_smtp_enabled` and
          :func:`app.services.mail.user_has_mail_privileges` for feature flags.
    """

    quote_email_smtp_enabled = is_quote_email_smtp_enabled()
    user_can_send_quote_email = user_has_mail_privileges(current_user)
    threshold_warning = check_thresholds(quote.quote_type, quote.weight, quote.total)
    exceeds_threshold = bool(threshold_warning)

    return {
        "quote": quote,
        "metadata": metadata,
        "exceeds_threshold": exceeds_threshold,
        "can_request_booking_email": bool(
            getattr(current_user, "is_authenticated", False)
        ),
        "can_send_quote_email": (
            user_can_send_quote_email and quote_email_smtp_enabled
        ),
        "quote_email_smtp_enabled": quote_email_smtp_enabled,
        "user_can_send_quote_email": user_can_send_quote_email,
    }


def _format_quote_copy_email_body(
    quote: Quote,
    *,
    metadata: dict[str, object],
    return_quote_requested: bool,
) -> str:
    """Compose plain-text quote details for the self-email workflow.

    Args:
        quote: Quote instance being emailed.
        metadata: Parsed ``quote.quote_metadata`` payload that may include
            accessorial pricing entries.
        return_quote_requested: Whether the user checked the return quote box
            on ``templates/quote_result.html``.

    Returns:
        A receipt-style plain-text body suitable for SMTP delivery.

    """

    raw_accessorials = metadata.get("accessorials")
    accessorials = raw_accessorials if isinstance(raw_accessorials, dict) else {}
    accessorial_total = float(metadata.get("accessorial_total", 0.0) or 0.0)
    base_charge = max(float(quote.total or 0.0) - accessorial_total, 0.0)

    accessorial_names = ", ".join(accessorials.keys()) or "None"
    return_text = "YES" if return_quote_requested else "NO"

    lines = [
        "QUOTE DETAILS",
        "==========================================",
        f"Quote ID: {quote.quote_id}",
        f"Return Quote: {return_text}",
        "",
        "SHIPMENT SPECIFICATIONS",
        "------------------------------------------",
        f"Origin: {quote.origin}",
        f"Destination: {quote.destination}",
        f"Pieces: {quote.pieces}",
        f"Weight: {float(quote.weight or 0.0):.2f} lbs ({quote.weight_method})",
        f"Accessorials: {accessorial_names}",
        "",
        "PRICING BREAKDOWN",
        "------------------------------------------",
        f"Base Charge: $ {base_charge:.2f}",
    ]

    for name, raw_cost in accessorials.items():
        safe_name = str(name)[:22]
        cost = float(raw_cost or 0.0)
        lines.append(f"{safe_name.ljust(23)} $ {cost:.2f}")

    lines.extend(
        [
            "------------------------------------------",
            f"TOTAL: $ {float(quote.total or 0.0):.2f}",
            "==========================================",
            "",
            "Return to Quote Tool: https://quote.freightservices.net",
            "Unsubscribe: https://quote.freightservices.net/help",
        ]
    )

    return "\n".join(lines)


def _render_email_request(
    quote_id: str,
    *,
    admin_fee: float,
    heading_text: str,
    intro_line: str,
    subject_prefix: str,
) -> str:
    """Return the email request form configured for a specific workflow.

    Args:
        quote_id: Public identifier for the quote displayed in the form.
        admin_fee: Dollar amount added to the total for administrative costs.
        heading_text: Page heading shown above the request form.
        intro_line: First sentence inserted into the composed email body.
        subject_prefix: Text prefixed to the generated email subject line.

    Returns:
        A rendered ``email_request.html`` template.

    Notes:
        The :func:`quotes.email_request_form` route requires authentication via
        :func:`flask_login.login_required` before calling this helper.
    """

    quote = Quote.query.filter_by(quote_id=quote_id).first_or_404()
    metadata = json.loads(quote.quote_metadata or "{}")

    accessorial_total = float(metadata.get("accessorial_total", 0.0) or 0.0)
    metadata["accessorial_total"] = accessorial_total
    accessorials = metadata.get("accessorials") or {}
    if not isinstance(accessorials, dict):
        accessorials = {}
    accessorial_names = ", ".join(accessorials.keys())

    total_with_fee = float(quote.total or 0.0) + float(admin_fee)
    maps_api_key = (
        current_app.config.get("GOOGLE_MAPS_API_KEY")
        or os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("MAPS_API_KEY")
    )

    return render_template(
        "email_request.html",
        quote=quote,
        metadata=metadata,
        accessorial_names=accessorial_names,
        total_with_fee=total_with_fee,
        user_name=current_user.name or "",
        user_company=getattr(current_user, "company", "") or "",
        page_heading=heading_text,
        email_intro_line=intro_line,
        subject_prefix=subject_prefix,
        admin_fee=float(admin_fee),
        maps_api_key=maps_api_key,
    )


@quotes_bp.route("/<quote_id>/email", methods=["GET"])
@login_required
def email_request_form(quote_id: str):
    """Render the booking workflow email form for the provided quote."""

    return _render_email_request(
        quote_id,
        admin_fee=15.0,
        heading_text="Email Booking Request",
        intro_line="I'd like to go ahead and book the following quote",
        subject_prefix="New Booking request",
    )


@quotes_bp.route("/<quote_id>/email-volume", methods=["GET"])
@login_required
def email_volume_request_form(quote_id: str):
    """Render the volume pricing workflow email form for the provided quote."""

    return _render_email_request(
        quote_id,
        admin_fee=0.0,
        heading_text="Email Volume Pricing Request",
        intro_line="I'd like to move forward with volume pricing for the following quote",
        subject_prefix="Volume pricing request",
    )


@quotes_bp.route("/<quote_id>/email-self", methods=["POST"])
@login_required
def email_quote_to_me(quote_id: str):
    """Send a plain-text quote copy to the authenticated user email address.

    Args:
        quote_id: Public quote identifier from the route path.

    Returns:
        The refreshed ``quote_result.html`` page with a success or error flash.

    External dependencies:
        * Sends mail via :func:`app.services.mail.send_email`.
        * Renders ``templates/quote_result.html`` with
          :func:`_quote_template_context`.
    """

    quote = Quote.query.filter_by(quote_id=quote_id).first_or_404()
    metadata = json.loads(quote.quote_metadata or "{}")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["accessorial_total"] = float(metadata.get("accessorial_total", 0.0) or 0.0)

    return_quote_requested = request.form.get("return_quote") == "yes"
    email_body = _format_quote_copy_email_body(
        quote,
        metadata=metadata,
        return_quote_requested=return_quote_requested,
    )

    unsubscribe_link = "https://quote.freightservices.net/help"
    try:
        send_email(
            to=current_user.email,
            subject=f"Freight Services Inc.\nQuote Copy - {quote.quote_id}",
            body=email_body,
            user=current_user,
            feature="quote_copy",
            headers={
                "List-Unsubscribe": f"<{unsubscribe_link}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
        flash(f"Quote details sent to {current_user.email}", "success")
    except Exception:
        logger.exception("Failed to send quote copy for quote_id=%s", quote.quote_id)
        flash("Failed to send email. Please try again later.", "danger")

    return render_template(
        "quote_result.html", **_quote_template_context(quote, metadata)
    )
