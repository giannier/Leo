"""the real WhatsApp channel: contacts, raw webhooks, delivery history

Feature 002, TASK-201. Three new tables and three columns on `messages`.

The `conversations.contact_id` column already exists - Feature 001 created it
without a foreign key, because `contacts` did not exist yet. This migration
adds the constraint rather than the column. Any row written before now has it
null (the sandbox never sets it), so no backfill is needed and the constraint
is valid the moment it is created.

`messages.provider_message_id` is unique **globally**, not per tenant. It is
the provider's own identifier, so two tenants cannot legitimately share one,
and the constraint is what makes a redelivered webhook insert exactly once.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MESSAGE_TYPES = (
    "text",
    "image",
    "document",
    "audio",
    "sticker",
    "location",
    "contact",
    "unsupported",
)
DELIVERY_STATUSES = ("queued", "sent", "delivered", "read", "failed")


def _in_list(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("profile_name", sa.Text(), nullable=True),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_contacts_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
    )
    # One row per person per business. The same phone writing to two
    # businesses is two contacts, and neither may ever see the other.
    op.create_index(
        "uq_contacts_tenant_id_phone_e164",
        "contacts",
        ["tenant_id", "phone_e164"],
        unique=True,
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.Text(), server_default="ycloud", nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="received", nullable=False),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('ycloud')",
            name=op.f("ck_webhook_events_webhook_events_provider"),
        ),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'failed', 'dead_letter')",
            name=op.f("ck_webhook_events_webhook_events_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        # Idempotency, enforced by the database rather than by a SELECT in
        # front of the insert - which would race with the provider's retry.
        sa.UniqueConstraint("provider_event_id", name=op.f("uq_webhook_events_provider_event_id")),
    )
    op.create_index(
        "ix_webhook_events_status_created_at",
        "webhook_events",
        ["status", "created_at"],
    )

    op.create_table(
        "message_status_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_status_events_message_id_messages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_status_events")),
    )
    op.create_index(
        "ix_message_status_events_message_id_provider_ts",
        "message_status_events",
        ["message_id", "provider_ts"],
    )

    # `contact_id` was created by 0001 without a foreign key, because the
    # table it points at did not exist. Every existing row is null.
    op.create_foreign_key(
        op.f("fk_conversations_contact_id_contacts"),
        "conversations",
        "contacts",
        ["contact_id"],
        ["id"],
    )

    op.add_column("messages", sa.Column("provider_message_id", sa.Text(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_messages_provider_message_id"), "messages", ["provider_message_id"]
    )
    op.add_column(
        "messages", sa.Column("msg_type", sa.Text(), server_default="text", nullable=False)
    )
    op.add_column("messages", sa.Column("delivery_status", sa.Text(), nullable=True))
    op.create_check_constraint("messages_msg_type", "messages", _in_list("msg_type", MESSAGE_TYPES))
    op.create_check_constraint(
        "messages_delivery_status",
        "messages",
        _in_list("delivery_status", DELIVERY_STATUSES),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_messages_messages_delivery_status"), "messages", type_="check")
    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.drop_column("messages", "delivery_status")
    op.drop_column("messages", "msg_type")
    op.drop_constraint(op.f("uq_messages_provider_message_id"), "messages", type_="unique")
    op.drop_column("messages", "provider_message_id")

    op.drop_constraint(
        op.f("fk_conversations_contact_id_contacts"), "conversations", type_="foreignkey"
    )

    op.drop_index(
        "ix_message_status_events_message_id_provider_ts", table_name="message_status_events"
    )
    op.drop_table("message_status_events")
    op.drop_index("ix_webhook_events_status_created_at", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("uq_contacts_tenant_id_phone_e164", table_name="contacts")
    op.drop_table("contacts")
