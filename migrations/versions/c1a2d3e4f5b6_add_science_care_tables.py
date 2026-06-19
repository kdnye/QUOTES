"""add science care tables

Revision ID: c1a2d3e4f5b6
Revises: b3c4d5e6f7a8
Create Date: 2026-06-19 18:00:00.000000

Adds the Science Care multi-lab quote feature schema:

Reference tables (CSV round-tripable):
    sc_labs, sc_tissue_codes, sc_box_types, sc_consumables,
    sc_established_lanes, sc_accessorial_map

Submission tables (populated post-create_quote):
    sc_quote_sessions, sc_quote_session_legs

Also adds users.is_sc_admin (defaults False; flip it manually for the
first SC admin via the existing admin tooling).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1a2d3e4f5b6"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_sc_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "sc_labs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lab_code", sa.String(length=20), nullable=False),
        sa.Column("lab_name", sa.String(length=150)),
        sa.Column("origin_zip", sa.String(length=10), nullable=False),
        sa.Column("address", sa.String(length=250)),
        sa.Column("contact_name", sa.String(length=120)),
        sa.Column("contact_phone", sa.String(length=50)),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set", "lab_code", name="uq_sc_labs_rate_set_lab_code"
        ),
    )
    op.create_index(
        op.f("ix_sc_labs_rate_set"), "sc_labs", ["rate_set"], unique=False
    )

    op.create_table(
        "sc_tissue_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tissue_code", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=250)),
        sa.Column("unit_weight_lb", sa.Float(), nullable=False),
        sa.Column("default_box_type_code", sa.String(length=20)),
        sa.Column("pieces_per_box", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set", "tissue_code",
            name="uq_sc_tissue_codes_rate_set_tissue_code",
        ),
    )
    op.create_index(
        op.f("ix_sc_tissue_codes_rate_set"),
        "sc_tissue_codes",
        ["rate_set"],
        unique=False,
    )

    op.create_table(
        "sc_box_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=80)),
        sa.Column("length_in", sa.Float(), nullable=False),
        sa.Column("width_in", sa.Float(), nullable=False),
        sa.Column("height_in", sa.Float(), nullable=False),
        sa.Column(
            "tare_weight_lb", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("max_payload_lb", sa.Float()),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set", "code", name="uq_sc_box_types_rate_set_code"
        ),
    )
    op.create_index(
        op.f("ix_sc_box_types_rate_set"),
        "sc_box_types",
        ["rate_set"],
        unique=False,
    )

    op.create_table(
        "sc_consumables",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consumable_type", sa.String(length=30), nullable=False),
        sa.Column("temp_mode", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("weight_lb_per_box", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set",
            "consumable_type",
            "temp_mode",
            "scope",
            name="uq_sc_consumables_rate_set_type_mode_scope",
        ),
    )
    op.create_index(
        op.f("ix_sc_consumables_rate_set"),
        "sc_consumables",
        ["rate_set"],
        unique=False,
    )

    op.create_table(
        "sc_established_lanes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("origin_zip", sa.String(length=10), nullable=False),
        sa.Column("dest_zip", sa.String(length=10), nullable=False),
        sa.Column(
            "service_type",
            sa.String(length=10),
            nullable=False,
            server_default="Any",
        ),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set",
            "origin_zip",
            "dest_zip",
            "service_type",
            name="uq_sc_lanes_rate_set_origin_dest_service",
        ),
    )
    op.create_index(
        op.f("ix_sc_established_lanes_rate_set"),
        "sc_established_lanes",
        ["rate_set"],
        unique=False,
    )

    op.create_table(
        "sc_accessorial_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("form_field", sa.String(length=20), nullable=False),
        sa.Column("display_label", sa.String(length=150), nullable=False),
        sa.Column("accessorial_name", sa.String(length=120), nullable=False),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
        sa.UniqueConstraint(
            "rate_set",
            "form_field",
            name="uq_sc_accessorial_map_rate_set_form_field",
        ),
    )
    op.create_index(
        op.f("ix_sc_accessorial_map_rate_set"),
        "sc_accessorial_map",
        ["rate_set"],
        unique=False,
    )

    op.create_table(
        "sc_quote_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "submitted_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "grand_total", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("payload_json", sa.Text()),
        sa.Column(
            "rate_set",
            sa.String(length=50),
            nullable=False,
            server_default="science_care",
        ),
    )
    op.create_index(
        op.f("ix_sc_quote_sessions_user_id"),
        "sc_quote_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sc_quote_sessions_submitted_at"),
        "sc_quote_sessions",
        ["submitted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sc_quote_sessions_rate_set"),
        "sc_quote_sessions",
        ["rate_set"],
        unique=False,
    )

    op.create_table(
        "sc_quote_session_legs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("sc_quote_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("air_quote_id", sa.Integer(), sa.ForeignKey("quotes.id")),
        sa.Column(
            "hotshot_quote_id", sa.Integer(), sa.ForeignKey("quotes.id")
        ),
        sa.Column("established_rate", sa.Float()),
        sa.Column("winner_mode", sa.String(length=20)),
        sa.Column("winner_total", sa.Float(), server_default="0"),
        sa.Column("skip_reason", sa.String(length=60)),
        sa.UniqueConstraint(
            "session_id",
            "leg_index",
            name="uq_sc_quote_session_legs_session_id_leg_index",
        ),
    )
    op.create_index(
        op.f("ix_sc_quote_session_legs_session_id"),
        "sc_quote_session_legs",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sc_quote_session_legs_session_id"),
        table_name="sc_quote_session_legs",
    )
    op.drop_table("sc_quote_session_legs")

    op.drop_index(
        op.f("ix_sc_quote_sessions_rate_set"), table_name="sc_quote_sessions"
    )
    op.drop_index(
        op.f("ix_sc_quote_sessions_submitted_at"),
        table_name="sc_quote_sessions",
    )
    op.drop_index(
        op.f("ix_sc_quote_sessions_user_id"), table_name="sc_quote_sessions"
    )
    op.drop_table("sc_quote_sessions")

    op.drop_index(
        op.f("ix_sc_accessorial_map_rate_set"),
        table_name="sc_accessorial_map",
    )
    op.drop_table("sc_accessorial_map")

    op.drop_index(
        op.f("ix_sc_established_lanes_rate_set"),
        table_name="sc_established_lanes",
    )
    op.drop_table("sc_established_lanes")

    op.drop_index(
        op.f("ix_sc_consumables_rate_set"), table_name="sc_consumables"
    )
    op.drop_table("sc_consumables")

    op.drop_index(
        op.f("ix_sc_box_types_rate_set"), table_name="sc_box_types"
    )
    op.drop_table("sc_box_types")

    op.drop_index(
        op.f("ix_sc_tissue_codes_rate_set"), table_name="sc_tissue_codes"
    )
    op.drop_table("sc_tissue_codes")

    op.drop_index(op.f("ix_sc_labs_rate_set"), table_name="sc_labs")
    op.drop_table("sc_labs")

    op.drop_column("users", "is_sc_admin")
