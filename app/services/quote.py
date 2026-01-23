"""Database-backed services for calculating and retrieving shipping quotes.

This module coordinates between quoting logic, threshold checks, and the
SQLAlchemy models defined in :mod:`app.database` to create, persist, and
retrieve quote records for the application.
"""

import json

from app.database import Session, Quote, EmailQuoteRequest, Accessorial
from app.quote.logic_hotshot import calculate_hotshot_quote
from app.quote.logic_air import calculate_air_quote
from app.quote.thresholds import check_thresholds
from app.services.rate_sets import DEFAULT_RATE_SET, normalize_rate_set


def get_accessorial_options(quote_type: str) -> list[str]:
    """Return list of accessorial names from the database."""
    with Session() as db:
        names = [a.name for a in db.query(Accessorial).all()]
    if quote_type == "Hotshot":
        names = [n for n in names if "guarantee" not in n.lower()]
    return names


def create_quote(
    user_id,
    user_email,
    quote_type,
    origin,
    destination,
    weight,
    accessorial_total=0.0,
    pieces=1,
    length=0.0,
    width=0.0,
    height=0.0,
    dim_weight=0.0,
    accessorials=None,
    rate_set: str | None = None,
):
    """Generate a quote and persist to the database.

    Populates :attr:`Quote.warnings` when the quote exceeds limits via
    :func:`quote.thresholds.check_thresholds`.
    """

    actual_weight = weight
    # Allow callers to provide a pre-computed dimensional weight.  If it is
    # not supplied (or is non-positive), compute it from the package
    # dimensions when available.
    if dim_weight and dim_weight > 0:
        dim_weight_val = float(dim_weight)
    elif all(v > 0 for v in [length, width, height]):
        dim_weight_val = (length * width * height / 166) * pieces
    else:
        dim_weight_val = 0.0

    billable_weight = max(actual_weight, dim_weight_val)
    weight_method = (
        "Dimensional"
        if billable_weight == dim_weight_val and dim_weight_val > 0
        else "Actual"
    )

    normalized_rate_set = normalize_rate_set(rate_set or DEFAULT_RATE_SET)

    accessorial_costs: dict[str, float] = {}
    guarantee_pct: float | None = None
    if accessorials:
        with Session() as db:
            acc_map = {a.name.lower(): a for a in db.query(Accessorial).all()}
        for raw_acc in accessorials:
            if not raw_acc:
                continue
            acc = str(raw_acc).strip()
            record = acc_map.get(acc.lower())
            if not record:
                continue
            if record.is_percentage:
                if record.name.lower() == "guarantee":
                    guarantee_pct = record.amount or 0.0
                else:
                    accessorial_costs[record.name] = record.amount or 0.0
                    accessorial_total += record.amount or 0.0
            else:
                cost = record.amount or 0.0
                accessorial_costs[record.name] = cost
                accessorial_total += cost

    if quote_type == "Air":
        result = calculate_air_quote(
            origin,
            destination,
            billable_weight,
            accessorial_total,
            rate_set=normalized_rate_set,
        )
    else:
        try:
            result = calculate_hotshot_quote(
                origin,
                destination,
                billable_weight,
                accessorial_total,
                rate_set=normalized_rate_set,
            )
        except ValueError as e:
            raise ValueError(f"Hotshot quote calculation failed: {e}")

    quote_total = result["quote_total"]
    if quote_type == "Air" and guarantee_pct:
        # Guarantee covers the linehaul and beyond charges, excluding other accessorials.
        linehaul_with_beyond = quote_total - accessorial_total
        guarantee_cost = linehaul_with_beyond * (guarantee_pct / 100.0)
        accessorial_costs["Guarantee"] = guarantee_cost
        accessorial_total += guarantee_cost
        quote_total += guarantee_cost

    warning = check_thresholds(quote_type, billable_weight, quote_total)

    metadata = {
        "accessorials": accessorial_costs,
        "accessorial_total": accessorial_total,
        "miles": result.get("miles"),
        "pieces": pieces,
        "details": {
            k: v for k, v in result.items() if k not in {"quote_total", "miles", "zone"}
        },
    }

    with Session() as db:
        q = Quote(
            user_id=user_id,
            user_email=user_email,
            quote_type=quote_type,
            origin=origin,
            destination=destination,
            weight=billable_weight,
            weight_method=weight_method,
            actual_weight=actual_weight,
            dim_weight=dim_weight,
            pieces=pieces,
            length=length,
            width=width,
            height=height,
            zone=str(result.get("zone", "")),
            total=quote_total,
            quote_metadata=json.dumps(metadata),
            rate_set=normalized_rate_set,
            warnings=warning,
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return q, metadata


def get_quote(quote_id: str):
    """Return the stored :class:`db.Quote` identified by ``quote_id``.

    Args:
        quote_id: The primary key of the quote record to look up.

    Returns:
        Quote | None: The matching :class:`db.Quote` instance if it exists, or
        ``None`` when the identifier is unknown.

    Side Effects:
        Opens a SQLAlchemy session using :class:`db.Session` and executes a
        database ``SELECT`` query.
    """

    with Session() as db:
        return db.query(Quote).filter_by(quote_id=quote_id).first()


def list_quotes():
    """Retrieve every persisted :class:`db.Quote` record.

    Returns:
        list[Quote]: All quote rows stored in the database ordered by the
        default SQLAlchemy query rules.

    Side Effects:
        Opens a SQLAlchemy session via :class:`db.Session` and performs a
        database ``SELECT`` query returning multiple rows.
    """

    with Session() as db:
        return db.query(Quote).all()


def create_email_request(quote_id: str, data: dict):
    """Persist a new :class:`db.EmailQuoteRequest` linked to an existing quote.

    Args:
        quote_id: The identifier for the parent :class:`db.Quote` record.
        data: Form values that supply shipper/consignee contact details. Expected
            keys include ``shipper_name``, ``shipper_address``,
            ``shipper_contact``, ``shipper_phone``, ``consignee_name``,
            ``consignee_address``, ``consignee_contact``, ``consignee_phone``,
            ``total_weight``, and ``special_instructions``.

    Returns:
        EmailQuoteRequest: The persisted :class:`db.EmailQuoteRequest` instance
        containing the provided contact information.

    Side Effects:
        Writes a new row to the database using :class:`db.Session` and commits
        the transaction.
    """

    with Session() as db:
        req = EmailQuoteRequest(
            quote_id=quote_id,
            shipper_name=data.get("shipper_name"),
            shipper_address=data.get("shipper_address"),
            shipper_contact=data.get("shipper_contact"),
            shipper_phone=data.get("shipper_phone"),
            consignee_name=data.get("consignee_name"),
            consignee_address=data.get("consignee_address"),
            consignee_contact=data.get("consignee_contact"),
            consignee_phone=data.get("consignee_phone"),
            total_weight=data.get("total_weight"),
            special_instructions=data.get("special_instructions"),
        )
        db.add(req)
        db.commit()
        return req
