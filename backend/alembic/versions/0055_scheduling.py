"""Appointments and visits with a calendar of its own (feature 019, phase D; spec 014).

Revision ID: 0055
Revises: 0054

* `products.agendable`, `duracion_min`, `buffer_min`: a service that can be booked. Nothing else about the
  catalogue changes: a bookable item can also be fixed-price or quoted.
* `resources`: who or what takes the appointment (a person, a chair, a room). Without its own `horario` it
  follows the business's opening hours, so the hours are written once.
* `resource_blocks`: lunch, holidays, maintenance.
* `appointments`: the calendar. The database itself refuses two live appointments that overlap on one resource
  (`EXCLUDE USING gist`), whatever the code does. `ocupa_hasta` is the end plus the buffer.
* `appointment_events`: append-only history.
* `scheduling_settings`: the rules (notice, horizon, how many live appointments a person may hold) and the
  reminders, which are off until the owner turns them on: each one is a message that costs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055"
down_revision: str | Sequence[str] | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE = "('agendada', 'confirmada')"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.add_column(
        "products", sa.Column("agendable", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column("products", sa.Column("duracion_min", sa.Integer(), nullable=True))
    op.add_column(
        "products", sa.Column("buffer_min", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint(
        op.f("ck_products_products_duracion"),
        "products",
        "duracion_min IS NULL OR duracion_min BETWEEN 15 AND 480",
    )
    op.create_check_constraint(
        op.f("ck_products_products_buffer"), "products", "buffer_min BETWEEN 0 AND 120"
    )
    op.create_check_constraint(
        op.f("ck_products_products_agendable_con_duracion"),
        "products",
        "NOT agendable OR duracion_min IS NOT NULL",
    )

    op.create_table(
        "resources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        # {"mon": [["09:00", "13:00"]], ...}; NULL follows the business's opening hours.
        sa.Column("horario", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Empty means it takes every bookable service.
        sa.Column(
            "product_ids",
            postgresql.ARRAY(sa.UUID()),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(nombre)) BETWEEN 1 AND 80",
            name=op.f("ck_resources_resources_nombre"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_resources_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
    )
    op.create_index(op.f("ix_resources_tenant_id"), "resources", ["tenant_id"])

    op.create_table(
        "resource_blocks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name=op.f("ck_resource_blocks_resource_blocks_range")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_resource_blocks_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            name=op.f("fk_resource_blocks_resource_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_blocks")),
    )
    op.create_index(op.f("ix_resource_blocks_tenant_id"), "resource_blocks", ["tenant_id"])
    op.create_index(op.f("ix_resource_blocks_resource_id"), "resource_blocks", ["resource_id"])

    op.create_table(
        "appointments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        # A walk-in or a phone call has no contact: the name and phone are written here.
        sa.Column("nombre_cliente", sa.Text(), nullable=True),
        sa.Column("telefono", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        # `ends_at` plus the service's buffer: what the exclusion constraint keeps free.
        sa.Column("ocupa_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default="agendada", nullable=False),
        # Raised on every change of time, so a reminder claimed for an older time is not sent.
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("recordatorio_24h_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recordatorio_2h_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('agendada', 'confirmada', 'completada', 'cancelada', 'no_asistio')",
            name=op.f("ck_appointments_appointments_status"),
        ),
        sa.CheckConstraint(
            "created_by IN ('agent', 'user')", name=op.f("ck_appointments_appointments_created_by")
        ),
        sa.CheckConstraint(
            "ends_at > starts_at AND ocupa_hasta >= ends_at",
            name=op.f("ck_appointments_appointments_range"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_appointments_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_appointments_product_id_products")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resources.id"], name=op.f("fk_appointments_resource_id_resources")
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name=op.f("fk_appointments_contact_id_contacts")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_appointments_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
    )
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT ex_appointments_resource_no_overlap "
        "EXCLUDE USING gist (resource_id WITH =, tstzrange(starts_at, ocupa_hasta) WITH &&) "
        f"WHERE (status IN {LIVE})"
    )
    op.create_index(
        "ix_appointments_tenant_id_starts_at", "appointments", ["tenant_id", "starts_at"]
    )
    op.create_index("ix_appointments_contact_id", "appointments", ["contact_id"])

    op.create_table(
        "appointment_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("appointment_id", sa.UUID(), nullable=False),
        sa.Column("evento", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "detalle",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_appointment_events_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name=op.f("fk_appointment_events_appointment_id_appointments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_events")),
    )
    op.create_index(
        op.f("ix_appointment_events_appointment_id"),
        "appointment_events",
        ["appointment_id", "created_at"],
    )

    op.create_table(
        "scheduling_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("anticipacion_min_horas", sa.Integer(), server_default="2", nullable=False),
        sa.Column("anticipacion_max_dias", sa.Integer(), server_default="30", nullable=False),
        sa.Column("max_citas_activas", sa.Integer(), server_default="2", nullable=False),
        sa.Column("recordatorio_24h", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("recordatorio_2h", sa.Boolean(), server_default="false", nullable=False),
        # The approved template that carries a reminder sent outside the 24-hour window.
        sa.Column("plantilla_recordatorio_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "anticipacion_min_horas BETWEEN 0 AND 72",
            name=op.f("ck_scheduling_settings_scheduling_settings_min"),
        ),
        sa.CheckConstraint(
            "anticipacion_max_dias BETWEEN 1 AND 180",
            name=op.f("ck_scheduling_settings_scheduling_settings_max"),
        ),
        sa.CheckConstraint(
            "max_citas_activas BETWEEN 1 AND 10",
            name=op.f("ck_scheduling_settings_scheduling_settings_active"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_scheduling_settings_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["plantilla_recordatorio_id"],
            ["message_templates.id"],
            name=op.f("fk_scheduling_settings_plantilla_recordatorio_id_message_templates"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_scheduling_settings")),
    )


def downgrade() -> None:
    op.drop_table("scheduling_settings")
    op.drop_table("appointment_events")
    op.drop_table("appointments")
    op.drop_table("resource_blocks")
    op.drop_table("resources")
    op.drop_constraint(
        op.f("ck_products_products_agendable_con_duracion"), "products", type_="check"
    )
    op.drop_constraint(op.f("ck_products_products_buffer"), "products", type_="check")
    op.drop_constraint(op.f("ck_products_products_duracion"), "products", type_="check")
    op.drop_column("products", "buffer_min")
    op.drop_column("products", "duracion_min")
    op.drop_column("products", "agendable")
