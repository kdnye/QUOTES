"""Centralizes database table names and SQLAlchemy models for the quoting app.

The module exposes string constants that define the canonical table names used
throughout migrations, raw SQL helpers, and other services. Those constants are
paired with SQLAlchemy models such as :class:`User`, :class:`Quote`,
:class:`EmailQuoteRequest`, and :class:`PasswordResetToken`, which describe the
schema and relationships for their respective tables.
"""

from datetime import datetime
import uuid
import secrets
import string
from typing import Optional
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Boolean, Enum, UniqueConstraint
from sqlalchemy.orm import Mapped
from werkzeug.security import generate_password_hash, check_password_hash


# Table name constants for easy reuse across the codebase
USERS_TABLE = "users"
QUOTES_TABLE = "quotes"
EMAIL_REQUESTS_TABLE = "email_quote_requests"
EMAIL_DISPATCH_LOG_TABLE = "email_dispatch_log"
PASSWORD_RESET_TOKENS_TABLE = "password_reset_tokens"
ACCESSORIALS_TABLE = "accessorials"
APP_SETTINGS_TABLE = "app_settings"
HOTSHOT_RATES_TABLE = "hotshot_rates"
BEYOND_RATES_TABLE = "beyond_rates"
AIR_COST_ZONES_TABLE = "air_cost_zones"
ZIP_ZONES_TABLE = "zip_zones"
COST_ZONES_TABLE = "cost_zones"
RATE_UPLOADS_TABLE = "rate_uploads"
RATE_SET_LOGOS_TABLE = "rate_set_logos"
FUEL_SURCHARGES_TABLE = "fuel_surcharges"
VSC_ZONES_TABLE = "vsc_zones"

# Science Care multi-lab quote tables.
SC_LABS_TABLE = "sc_labs"
SC_TISSUE_CODES_TABLE = "sc_tissue_codes"
SC_BOX_TYPES_TABLE = "sc_box_types"
SC_CONSUMABLES_TABLE = "sc_consumables"
SC_ESTABLISHED_LANES_TABLE = "sc_established_lanes"
SC_ACCESSORIAL_MAP_TABLE = "sc_accessorial_map"
SC_QUOTE_SESSIONS_TABLE = "sc_quote_sessions"
SC_QUOTE_SESSION_LEGS_TABLE = "sc_quote_session_legs"
SC_USER_LAB_SLOTS_TABLE = "sc_user_lab_slots"
SC_TISSUE_BOX_CAPACITY_TABLE = "sc_tissue_box_capacity"
SC_INTERNATIONAL_LANES_TABLE = "sc_international_lanes"
BOOKING_EMAIL_RECEIPTS_TABLE = "booking_email_receipts"

RATE_SET_DEFAULT = "default"
RATE_SET_SCIENCE_CARE = "science_care"

BOOKING_EMAIL_KIND_SC_MULTI = "sc_multi"
BOOKING_EMAIL_KIND_SC_MULTI_SELF = "sc_multi_self"
BOOKING_EMAIL_KIND_SINGLE_QUOTE = "single_quote"
BOOKING_EMAIL_STATUS_PENDING = "pending"
BOOKING_EMAIL_STATUS_SENT = "sent"
BOOKING_EMAIL_STATUS_FAILED = "failed"


def generate_readable_id():
    """Generates a unique, readable ID like 'Q-7X9B2A'."""
    # Use uppercase and digits, excluding ambiguous characters (I, 1, O, 0)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    # Generate 8 random characters
    suffix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"Q-{suffix}"


db = SQLAlchemy()


class User(UserMixin, db.Model):
    """Registered application user.

    Users can authenticate and create :class:`Quote` records. The model stores
    contact information collected during registration to support quoting and
    customer service follow-up.

    Attributes:
        first_name: User's given name collected from the registration form.
        last_name: User's family name collected from the registration form.
        phone: Primary phone number supplied by the user. Stored as free-form
            text because formatting varies by country.
        company_name: Company name associated with the user account.
        company_phone: Contact phone number for the user's company.
        role: Application role flag used to enable privileged employee or
            administrative features. Acceptable values are ``"customer"``,
            ``"employee"``, or ``"super_admin"`` and the field defaults to
            ``"customer"``.
        employee_approved: Boolean gating elevated employee-only features.
            Set to ``True`` when the account has been vetted for internal tool
            access.
        can_send_mail: Boolean toggle that explicitly permits the user to send
            outbound emails, bypassing role checks for mail-only workflows.
        theme_preference: Appearance mode persisted for the account.
            Accepted values are ``"auto"`` (follow system preference),
            ``"light"``, and ``"dark"``.
        admin_previous_role: Cached role restored when administrative access is
            revoked. Persisted only while :attr:`is_admin` is ``True``.
        admin_previous_employee_approved: Cached ``employee_approved`` value
            restored alongside :attr:`admin_previous_role` when demoting an
            administrator.
        api_approved: Boolean granting a user permission to use the JSON API.
            Set by an admin after verifying the integration request.
        api_enabled: Boolean toggling the user's API key on or off without
            revoking the key itself.
        api_key: Randomly-generated bearer token for per-user API access.
            Stored in plain text so admins can share it with customers.
            ``None`` until an admin generates the key.
    """

    __tablename__ = USERS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    name = db.Column(db.String(120))
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    phone = db.Column(db.String(50))
    company_name = db.Column(db.String(120))
    company_phone = db.Column(db.String(50))
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    role: Mapped[str] = db.Column(
        Enum("customer", "employee", "super_admin", name="user_role"),
        nullable=False,
        default="customer",
    )
    employee_approved: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    can_send_mail: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    show_cost_breakdown: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    theme_preference: Mapped[str] = db.Column(
        db.String(10), nullable=False, default="auto"
    )
    admin_previous_role: Mapped[Optional[str]] = db.Column(
        Enum("customer", "employee", name="user_admin_previous_role"),
        nullable=True,
    )
    admin_previous_employee_approved: Mapped[Optional[bool]] = db.Column(
        Boolean, nullable=True
    )
    is_active = db.Column(db.Boolean, default=True)
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )
    is_sc_admin: Mapped[bool] = db.Column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    api_approved: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    api_enabled: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    api_key: Mapped[Optional[str]] = db.Column(
        db.String(128), unique=True, nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password: str) -> None:
        """Hash ``raw_password`` using
        :func:`werkzeug.security.generate_password_hash`.

        Args:
            raw_password: Plain text password provided by the user.

        Returns:
            None. The hashed value is stored on ``self.password_hash``.
        """

        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Validate ``raw_password`` against the stored hash using
        :func:`werkzeug.security.check_password_hash`.

        Args:
            raw_password: Plain text password to compare.

        Returns:
            ``True`` when the supplied password matches the stored hash;
            otherwise ``False``.
        """

        return check_password_hash(self.password_hash, raw_password)


class Quote(db.Model):
    """Shipping quote generated by a :class:`User`.

    Stores shipment details and totals and may be linked to an
    :class:`EmailQuoteRequest`.
    """

    __tablename__ = QUOTES_TABLE
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_reference",
            name="uq_quotes_user_id_client_reference",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(
        db.String(36), default=generate_readable_id, unique=True
    )  # public UUID for external reference
    user_id = db.Column(db.Integer, db.ForeignKey(f"{USERS_TABLE}.id"))
    user_email = db.Column(db.String(100))  # cached user email for quick access
    quote_type = db.Column(db.String(20), nullable=False)
    origin = db.Column(db.String(20))
    destination = db.Column(db.String(20))
    weight = db.Column(db.Float)
    weight_method = db.Column(db.String(20))
    actual_weight = db.Column(db.Float)
    dim_weight = db.Column(db.Float)
    pieces = db.Column(db.Integer, default=1)
    length = db.Column(db.Float, default=0.0)
    width = db.Column(db.Float, default=0.0)
    height = db.Column(db.Float, default=0.0)
    zone = db.Column(db.String(5))
    total = db.Column(db.Float, default=0.0)
    quote_metadata = db.Column(db.Text)  # JSON-encoded pricing metadata
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )
    # Optional customer-provided reference, unique per user when present.
    client_reference = db.Column(db.String(64), nullable=True, index=True)
    # IP address of the client that requested this quote (optional)
    request_ip = db.Column(db.String(45), nullable=True, index=True)
    # Origin of the quote: "web", "api_key" (per-user API key), or "api_service" (global token)
    quote_source = db.Column(db.String(20), nullable=True, index=True)
    warnings = db.Column(db.Text)  # calculation warnings shown to the user
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="quotes")  # parent user relationship


class EmailQuoteRequest(db.Model):
    """Supplemental details for a quote submitted via email.

    Linked to :class:`Quote` through ``quote_id``.
    """

    __tablename__ = EMAIL_REQUESTS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(
        db.String(36), db.ForeignKey(f"{QUOTES_TABLE}.quote_id"), nullable=False
    )  # references Quote.quote_id
    shipper_name = db.Column(db.String)
    shipper_address = db.Column(db.String)
    shipper_contact = db.Column(db.String)
    shipper_phone = db.Column(db.String)
    consignee_name = db.Column(db.String)
    consignee_address = db.Column(db.String)
    consignee_contact = db.Column(db.String)
    consignee_phone = db.Column(db.String)
    total_weight = db.Column(db.Float)
    special_instructions = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmailDispatchLog(db.Model):
    """Audit trail for outbound emails sent by the application.

    Rows are created by :func:`app.send_email` via
    :func:`services.mail.log_email_dispatch` to support rate limiting and
    troubleshooting. Each entry associates an optional :class:`User` with a
    feature label and recipient address.
    """

    __tablename__ = EMAIL_DISPATCH_LOG_TABLE

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey(f"{USERS_TABLE}.id"))
    feature = db.Column(db.String(50), nullable=False)
    recipient = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")


class AppSetting(db.Model):
    """Database-persisted configuration override.

    Attributes:
        key: Unique identifier for the setting (for example, ``"mail_username"``).
        value: Optional string payload stored for the key.
        is_secret: Flags whether the value should be hidden in administrative UIs.
        created_at: UTC timestamp when the row was created.
        updated_at: UTC timestamp automatically refreshed on modification.
    """

    __tablename__ = APP_SETTINGS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(255), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    is_secret = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PasswordResetToken(db.Model):
    """One-time token used to reset a user's password.

    Associated with a single :class:`User`. The ``token`` column stores the
    SHA-256 digest generated by :func:`services.auth_utils.hash_reset_token` so
    leaked database rows do not expose usable reset links.
    """

    __tablename__ = PASSWORD_RESET_TOKENS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey(f"{USERS_TABLE}.id"), nullable=False)
    token = db.Column(
        db.String(128), unique=True, nullable=False
    )  # hashed token value (SHA-256 hex digest)
    expires_at = db.Column(db.DateTime, nullable=False)  # UTC expiration timestamp
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used = db.Column(db.Boolean, default=False)
    user = db.relationship("User")


class Accessorial(db.Model):
    """Optional surcharge that can be applied to a :class:`Quote`."""

    __tablename__ = ACCESSORIALS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=True)  # fixed dollar amount for the charge
    is_percentage = db.Column(
        db.Boolean, nullable=False, default=False
    )  # True if ``amount`` is a percentage of base


class HotshotRate(db.Model):
    """Rate table entries for expedited (hotshot) shipments.

    ``weight_break`` may be ``None`` when a rate does not specify a break
    point. The ``per_mile`` column is stored but only used for special
    zone ``"X"`` calculations; standard zones rely solely on ``per_lb`` and
    ``min_charge``. See
    :func:`scripts.import_hotshot_rates.load_hotshot_rates` for how raw
    spreadsheet values are normalized.
    """

    __tablename__ = HOTSHOT_RATES_TABLE

    id = db.Column(db.Integer, primary_key=True)
    miles = db.Column(db.Integer, nullable=False)  # distance tier
    zone = db.Column(db.String(5), nullable=False)
    per_lb = db.Column(db.Float, nullable=False)
    per_mile = db.Column(db.Float, nullable=True)  # optional per-mile rate
    min_charge = db.Column(db.Float, nullable=False)
    weight_break = db.Column(
        db.Float, nullable=True
    )  # optional weight threshold for base rate
    fuel_pct = db.Column(db.Float, nullable=False)  # fuel surcharge percentage
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )


class BeyondRate(db.Model):
    """Flat beyond charges applied to shipments outside standard zones."""

    __tablename__ = BEYOND_RATES_TABLE

    id = db.Column(db.Integer, primary_key=True)
    zone = db.Column(db.String(5), nullable=False)
    rate = db.Column(db.Float, nullable=False)
    up_to_miles = db.Column(db.Float, nullable=False)
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )


class AirCostZone(db.Model):
    """Rate information for air shipments by zone."""

    __tablename__ = AIR_COST_ZONES_TABLE

    __table_args__ = (
        UniqueConstraint("rate_set", "zone", name="uq_air_cost_zones_rate_set_zone"),
    )

    id = db.Column(db.Integer, primary_key=True)
    zone = db.Column(db.String(5), nullable=False)
    min_charge = db.Column(db.Float, nullable=False)
    per_lb = db.Column(db.Float, nullable=False)
    weight_break = db.Column(db.Float, nullable=False)
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )


class ZipZone(db.Model):
    """Maps ZIP codes to Air Cost destination zones.

    This table supports air linehaul routing via ``dest_zone`` and must not be
    used for variable surcharge (VSC) lookups. VSC zone assignments are stored
    separately in :class:`VscZone` to keep surcharge policy independent from
    Air Cost routing definitions.
    """

    __tablename__ = ZIP_ZONES_TABLE

    __table_args__ = (
        UniqueConstraint("rate_set", "zipcode", name="uq_zip_zones_rate_set_zipcode"),
    )

    id = db.Column(db.Integer, primary_key=True)
    zipcode = db.Column(db.String(10), nullable=False)
    dest_zone = db.Column(db.Integer, nullable=False)
    beyond = db.Column(db.String(20))  # indicator for beyond-area surcharges
    notes = db.Column(db.Text, nullable=True)
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )


class CostZone(db.Model):
    """Lookup for cost zones based on origin/destination pairs."""

    __tablename__ = COST_ZONES_TABLE

    __table_args__ = (
        UniqueConstraint("rate_set", "concat", name="uq_cost_zones_rate_set_concat"),
    )

    id = db.Column(db.Integer, primary_key=True)
    concat = db.Column(db.String(5), nullable=False)  # concatenated origin/dest key
    cost_zone = db.Column(db.String(5), nullable=False)  # resulting cost zone code
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )


class VscZone(db.Model):
    """Maps ZIP codes to Variable Surcharge (VSC) zones.

    Inputs:
        zipcode: Normalized 5-digit ZIP lookup key.
        vsc_zone: Integer VSC zone value constrained to the range 1..10.
        rate_set: Named rate-set context used for primary/fallback lookups.

    Outputs:
        A domain-specific lookup row used by quote logic to resolve VSC
        percentages without reading Air Cost routing fields.

    External dependencies:
        * Queried by :func:`app.quote.logic_air.get_vsc_zone_for_zip`.
    """

    __tablename__ = VSC_ZONES_TABLE

    __table_args__ = (
        UniqueConstraint("rate_set", "zipcode", name="uq_vsc_zones_rate_set_zipcode"),
    )

    id = db.Column(db.Integer, primary_key=True)
    zipcode = db.Column(db.String(5), nullable=False)
    vsc_zone = db.Column(db.Integer, nullable=False)
    rate_set = db.Column(
        db.String(50), nullable=False, default=RATE_SET_DEFAULT, index=True
    )


class FuelSurcharge(db.Model):
    """Fuel surcharge percentage tracked by PADD region.

    Inputs:
        padd_region: Petroleum Administration for Defense District (PADD)
            region label used by internal pricing workflows.
        current_rate: Current surcharge percentage applied for the region.

    Outputs:
        Persisted row used by quoting services to resolve the active fuel
        surcharge for a specific PADD region.

    External dependencies:
        * Read by downstream pricing workflows that query
          :class:`FuelSurcharge` directly through SQLAlchemy.
    """

    __tablename__ = FUEL_SURCHARGES_TABLE

    id = db.Column(db.Integer, primary_key=True)
    padd_region = db.Column(db.String(50), unique=True, nullable=False, index=True)
    current_rate = db.Column(db.Float, nullable=False)
    last_updated = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class RateUpload(db.Model):
    """Audit log for uploaded rate CSV files."""

    __tablename__ = RATE_UPLOADS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(50), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RateSetLogo(db.Model):
    """Map a rate set to a customer logo file stored in mounted cloud storage.

    Inputs:
        rate_set: Normalized rate set identifier used to match the authenticated
            user's ``rate_set`` value.
        filename: Exact logo filename expected within the mounted
            ``CUSTOMER_LOGOS_DIR`` directory.

    Outputs:
        Persisted mapping row consumed by ``app.__init__.get_customer_logo``.

    External dependencies:
        * Queried by :func:`app.__init__.get_customer_logo` to resolve the file
          that should be returned by :func:`flask.send_file`.
    """

    __tablename__ = RATE_SET_LOGOS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    rate_set = db.Column(db.String(50), unique=True, nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


# ============================================================================
# Science Care multi-lab quote tables.
#
# Reference tables (CSV round-tripable via /sc/reference): SCLab, SCTissueCode,
# SCBoxType, SCConsumable, SCEstablishedLane, SCAccessorialMap.
# Submission tables (populated post-create_quote, never edited via CSV):
# SCQuoteSession, SCQuoteSessionLeg.
#
# All carry the existing `rate_set` partition column so the tables follow the
# same multi-tenant pattern as the rate models above. The default value
# RATE_SET_SCIENCE_CARE makes the SC use case the canonical tenant while
# leaving room for additional rate-set partitions if other customers ever
# adopt the same workflow.
# ============================================================================


class SCLab(db.Model):
    """Science Care lab → origin ZIP plus contact metadata."""

    __tablename__ = SC_LABS_TABLE
    __table_args__ = (
        UniqueConstraint("rate_set", "lab_code", name="uq_sc_labs_rate_set_lab_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    lab_code = db.Column(db.String(20), nullable=False)
    lab_name = db.Column(db.String(150))
    origin_zip = db.Column(db.String(10), nullable=False)
    address = db.Column(db.String(250))
    contact_name = db.Column(db.String(120))
    contact_phone = db.Column(db.String(50))
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCTissueCode(db.Model):
    """Per-tissue weight + default box-type allocation hint."""

    __tablename__ = SC_TISSUE_CODES_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "tissue_code",
            name="uq_sc_tissue_codes_rate_set_tissue_code",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    tissue_code = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(250))
    unit_weight_lb = db.Column(db.Float, nullable=False)
    default_box_type_code = db.Column(db.String(20))
    pieces_per_box = db.Column(db.Integer)
    notes = db.Column(db.Text)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCBoxType(db.Model):
    """Allowed shipment box types with dimensions + tare weight."""

    __tablename__ = SC_BOX_TYPES_TABLE
    __table_args__ = (
        UniqueConstraint("rate_set", "code", name="uq_sc_box_types_rate_set_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    label = db.Column(db.String(80))
    length_in = db.Column(db.Float, nullable=False)
    width_in = db.Column(db.Float, nullable=False)
    height_in = db.Column(db.Float, nullable=False)
    tare_weight_lb = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0"
    )
    max_payload_lb = db.Column(db.Float)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCTissueBoxCapacity(db.Model):
    """How many pieces of a tissue code fit in a given box type.

    Replaces the legacy single-choice (default_box_type_code, pieces_per_box)
    pair on :class:`SCTissueCode` with a full per-(tissue, box) matrix that
    matches the customer-supplied template (avg weight + qty per Medium /
    Large / X-Large / Small Airtray / Airtray).

    A missing row OR a row with ``pieces_per_box <= 0`` means the box type
    cannot be used to ship that tissue. The allocator picks the box that
    minimises ``ceil(qty / pieces_per_box)``, ties broken by smaller box
    interior volume.
    """

    __tablename__ = SC_TISSUE_BOX_CAPACITY_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "tissue_code",
            "box_code",
            name="uq_sc_tissue_box_capacity_rate_set_tissue_box",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    tissue_code = db.Column(db.String(40), nullable=False)
    box_code = db.Column(db.String(20), nullable=False)
    pieces_per_box = db.Column(db.Integer, nullable=False)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCConsumable(db.Model):
    """Frozen / RTU dry-ice & gel-pack weight additions per box."""

    __tablename__ = SC_CONSUMABLES_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "consumable_type",
            "temp_mode",
            "scope",
            name="uq_sc_consumables_rate_set_type_mode_scope",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    consumable_type = db.Column(db.String(30), nullable=False)
    temp_mode = db.Column(db.String(20), nullable=False)
    scope = db.Column(db.String(20), nullable=False)
    weight_lb_per_box = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCEstablishedLane(db.Model):
    """Pre-negotiated lab-to-lab freight rates.

    ``service_type`` may be ``Air``, ``Hotshot``, or ``Any``. ``Any`` rows
    participate in the cheapest-of rollup for both quote modes.
    """

    __tablename__ = SC_ESTABLISHED_LANES_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "origin_zip",
            "dest_zip",
            "service_type",
            name="uq_sc_lanes_rate_set_origin_dest_service",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    origin_zip = db.Column(db.String(10), nullable=False)
    dest_zip = db.Column(db.String(10), nullable=False)
    # Optional metro fallback. When both columns are set, the SC quote
    # service matches this lane for any leg whose dest_zip resolves to
    # the same (city, state) via Zipcode_Zones.csv - mirroring the
    # workbook's lab+"City,State" VLOOKUP.
    dest_city = db.Column(db.String(80), nullable=True)
    dest_state = db.Column(db.String(2), nullable=True)
    service_type = db.Column(
        db.String(10), nullable=False, default="Any", server_default="Any"
    )
    rate = db.Column(db.Float, nullable=False)
    effective_from = db.Column(db.Date)
    effective_to = db.Column(db.Date)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCInternationalLane(db.Model):
    """One pre-negotiated international air lane keyed by (destination, lab).

    Mirrors the ``International Quotes`` tab of the FSI Shipping Quote Tool
    2026 VSC-Locked workbook (``B4:O1102``). Each row is one combination of
    a destination city (display string like ``"Australia - Adelaide"``)
    and an origin SC lab (``SCAZ`` / ``SCCA`` / ...).

    Quote math (workbook ``R21``):
        IF(weight > weight_break,
           ((weight - weight_break) * per_lb) + min_charge,
           min_charge)
        + intl_hotshot_surcharge

    Where ``intl_hotshot_surcharge`` is non-zero only when:
        * notes == "Door to Door"
        * standard rate (not customer-specific or ground)
        * distance from destination city to airport > 80 km

    and equals ``(km_to_airport - 80) * cost_per_km_over_80``.

    No VSC, no accessorials, no fuel surcharge — the workbook prices these
    lanes net (one of the reasons quotes >= $750 require operator
    confirmation per ``Z11``).
    """

    __tablename__ = SC_INTERNATIONAL_LANES_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "destination",
            "lab_code",
            name="uq_sc_intl_lanes_rate_set_dest_lab",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    destination = db.Column(db.String(120), nullable=False, index=True)
    country = db.Column(db.String(80), nullable=False)
    notes = db.Column(db.String(40), nullable=True)  # "Door to Door" / "Door to Airport"
    rate_class = db.Column(
        db.String(40), nullable=False, default="Standard", server_default="Standard"
    )
    lab_code = db.Column(db.String(8), nullable=False, index=True)
    airport_code_1 = db.Column(db.String(8), nullable=True)
    airport_code_2 = db.Column(db.String(8), nullable=True)
    airport_code_3 = db.Column(db.String(8), nullable=True)
    min_charge = db.Column(db.Float, nullable=False)
    per_lb = db.Column(db.Float, nullable=False)
    weight_break = db.Column(db.Float, nullable=False)
    cost_per_km_over_80 = db.Column(db.Float, nullable=True)
    special_notes = db.Column(db.Text, nullable=True)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCAccessorialMap(db.Model):
    """Maps SC form fields (e.g. ``J3``) to live accessorial names."""

    __tablename__ = SC_ACCESSORIAL_MAP_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "form_field",
            name="uq_sc_accessorial_map_rate_set_form_field",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    form_field = db.Column(db.String(20), nullable=False)
    display_label = db.Column(db.String(150), nullable=False)
    accessorial_name = db.Column(db.String(120), nullable=False)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class SCQuoteSession(db.Model):
    """One row per submission of the SC multi-leg quote page."""

    __tablename__ = SC_QUOTE_SESSIONS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey(f"{USERS_TABLE}.id"), nullable=False, index=True
    )
    submitted_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        server_default=db.func.now(),
        nullable=False,
        index=True,
    )
    grand_total = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0"
    )
    payload_json = db.Column(db.Text)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )
    # Unified reference that ties together every leg of a multi-leg SC
    # submission. Auto-assigned as ``SCMQ0001``, ``SCMQ0002``, … when the
    # form leaves the field blank; otherwise honours a customer-supplied
    # value (validated by ``_normalize_client_reference``). Indexed
    # because both the booking-email and lookup endpoints scan by it.
    multi_reference = db.Column(
        db.String(64), unique=True, index=True, nullable=True
    )
    # Booking-intake form data: pickup/delivery dates plus
    # shipper/consignee blocks (name, street, city, state, zip,
    # contact, reference, phone, notes) captured on
    # /sc/quote/<id>/email-ops/intake before the composer page is
    # shown. Stored as JSON so the schema can evolve without a new
    # migration each time the intake form gains a field. ``None``
    # until the user submits the intake form for the first time.
    booking_intake_json = db.Column(db.Text, nullable=True)


class SCQuoteSessionLeg(db.Model):
    """One row per shipment leg of an :class:`SCQuoteSession`.

    Links the leg to the underlying Air / Hotshot :class:`Quote` rows
    produced by ``app.services.quote.create_quote``.
    """

    __tablename__ = SC_QUOTE_SESSION_LEGS_TABLE
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "leg_index",
            name="uq_sc_quote_session_legs_session_id_leg_index",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SC_QUOTE_SESSIONS_TABLE}.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leg_index = db.Column(db.Integer, nullable=False)
    air_quote_id = db.Column(
        db.Integer, db.ForeignKey(f"{QUOTES_TABLE}.id", ondelete="SET NULL")
    )
    hotshot_quote_id = db.Column(
        db.Integer, db.ForeignKey(f"{QUOTES_TABLE}.id", ondelete="SET NULL")
    )
    established_rate = db.Column(db.Float)
    winner_mode = db.Column(db.String(20))
    winner_total = db.Column(db.Float, default=0.0, server_default="0")
    skip_reason = db.Column(db.String(60))
    # JSON map of {consumable_id: qty} the user picked on the form. NULL
    # for legs submitted before the per-leg consumables feature shipped.
    consumables_json = db.Column(db.Text)
    # JSON map of {box_type_code: count} the user ended up with for the
    # leg (typed overrides win; falls back to the auto allocation from
    # the tissue rows). NULL for legs submitted before this feature
    # shipped.
    boxes_json = db.Column(db.Text)


class SCUserLabSlot(db.Model):
    """Per-user default lab assigned to each shipment slot on the SC form.

    Stores up to ``SC_LEG_COUNT`` rows per user. When the SC quote page
    renders, the ``lab_code_<n>`` inputs are prefilled from these
    rows so the user does not have to retype the same labs on every
    visit. The user can still override any slot per submission - this
    table is the *default*, not a hard constraint.
    """

    __tablename__ = SC_USER_LAB_SLOTS_TABLE
    __table_args__ = (
        UniqueConstraint(
            "rate_set",
            "user_id",
            "leg_index",
            name="uq_sc_user_lab_slots_rate_set_user_leg",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{USERS_TABLE}.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leg_index = db.Column(db.Integer, nullable=False)
    lab_code = db.Column(db.String(20), nullable=False)
    rate_set = db.Column(
        db.String(50),
        nullable=False,
        default=RATE_SET_SCIENCE_CARE,
        server_default=RATE_SET_SCIENCE_CARE,
        index=True,
    )


class BookingEmailReceipt(db.Model):
    """Audit trail for booking emails dispatched via Postmark/SMTP.

    One row per attempted send from the SC multi-leg composer
    (``/sc/quote/<id>/email-ops``) or the single-quote composer
    (``/quotes/<id>/email``). Stores who sent it, where it went, the
    subject line, and either the Postmark message id (on success) or
    the failure reason (on error). The lookup page and admin tooling
    can join against ``kind`` + ``reference`` to surface "last sent at
    X by Y" without re-querying the upstream SC session or Quote row.
    """

    __tablename__ = BOOKING_EMAIL_RECEIPTS_TABLE

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    reference = db.Column(db.String(120), nullable=False, index=True)
    sender_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{USERS_TABLE}.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sent_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        server_default=db.func.now(),
        nullable=False,
        index=True,
    )
    to_addr = db.Column(db.String(255), nullable=False)
    cc_addr = db.Column(db.String(255), nullable=True)
    subject = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, index=True)
    error_text = db.Column(db.Text, nullable=True)
    # Reserved for the Postmark message id. The current SMTP transport
    # in :mod:`app.services.mail` does not surface the
    # ``X-PM-Message-Id`` reply header, so this column stays ``NULL``
    # for now. The column is kept on the schema so the audit pipeline
    # is forward-compatible: when ``send_email`` is extended to return
    # the SMTP response, the routes will populate this without a
    # follow-up migration.
    postmark_message_id = db.Column(db.String(120), nullable=True)
