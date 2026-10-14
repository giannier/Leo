"""Inbox: takeover, unread, notes, notification settings and search (Feature 007, TASK-701)

* `conversations`: who has it (`takeover_user_id`, `takeover_started_at`,
  `takeover_activity_at` for the auto-return), `closed_at`, `last_message_at` (the
  inbox's order) and `unread_count` (inbound messages nobody has looked at).
* `messages.msg_type` gains `event`: takeover, return, close and payment decisions
  are items of the thread (`author='system'`, the details in `payload`).
* `conversation_notes`: internal notes, never sent to the customer.
* `notification_settings`: minutes before an unattended handover is emailed, and
  minutes of inactivity before a takeover returns to the agent (NULL = never).
* A Spanish full-text GIN index over `messages.content` for the search.

Web Push (`push_subscriptions`) is not here: it needs a new dependency (VAPID
signing), proposed in the 007 plan and waiting for approval.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "text",
    "image",
    "document",
    "audio",
    "sticker",
    "location",
    "contact",
    "unsupported",
    "video",
    "interactive",
)
NEW_TYPES = (*OLD_TYPES, "event")


def _types(values: Sequence[str]) -> str:
    return "msg_type IN (" + ", ".join(f"'{v}'" for v in values) + ")"


KINDS_BEFORE = (
    "trial_ending_3d",
    "trial_ending_1d",
    "trial_ended",
    "wizard_reminder",
    "quota_80",
    "nuevo_comprobante",
    "pedido_contra_entrega",
    "derivacion",
    "ventana_cerrada",
    "llm_caido",
)
KINDS_AFTER = (*KINDS_BEFORE, "derivacion_sin_atender")


def _kinds(values: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_tenant_notifications_tenant_notifications_kind"),
        "tenant_notifications",
        type_="check",
    )
    op.create_check_constraint(
        "tenant_notifications_kind", "tenant_notifications", _kinds(KINDS_AFTER)
    )
    op.add_column(
        "conversations",
        sa.Column("takeover_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("conversations", sa.Column("takeover_started_at", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("takeover_activity_at", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("last_message_at", sa.DateTime(timezone=True)))
    op.add_column(
        "conversations",
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE conversations c SET last_message_at = m.last FROM ("
        "SELECT conversation_id, max(created_at) AS last FROM messages GROUP BY conversation_id"
        ") m WHERE m.conversation_id = c.id"
    )
    op.execute(
        "UPDATE conversations SET last_message_at = created_at WHERE last_message_at IS NULL"
    )
    op.create_index(
        "ix_conversations_tenant_id_last_message_at",
        "conversations",
        ["tenant_id", "last_message_at"],
    )

    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.create_check_constraint("messages_msg_type", "messages", _types(NEW_TYPES))
    op.execute(
        "CREATE INDEX ix_messages_content_search ON messages "
        "USING gin (to_tsvector('spanish', content))"
    )

    op.create_table(
        "conversation_notes",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.UUID(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("author_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 2000", name="content"),
    )
    op.create_index(
        "ix_conversation_notes_tenant_id_conversation_id",
        "conversation_notes",
        ["tenant_id", "conversation_id"],
    )

    op.create_table(
        "notification_settings",
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("unattended_queue_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("auto_return_minutes", sa.Integer(), nullable=True, server_default="30"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "unattended_queue_minutes BETWEEN 1 AND 1440", name="unattended_queue_minutes"
        ),
        sa.CheckConstraint(
            "auto_return_minutes IS NULL OR auto_return_minutes BETWEEN 5 AND 1440",
            name="auto_return_minutes",
        ),
    )


def downgrade() -> None:
    op.execute("DELETE FROM tenant_notifications WHERE kind = 'derivacion_sin_atender'")
    op.drop_constraint(
        op.f("ck_tenant_notifications_tenant_notifications_kind"),
        "tenant_notifications",
        type_="check",
    )
    op.create_check_constraint(
        "tenant_notifications_kind", "tenant_notifications", _kinds(KINDS_BEFORE)
    )
    op.drop_table("notification_settings")
    op.drop_index("ix_conversation_notes_tenant_id_conversation_id", "conversation_notes")
    op.drop_table("conversation_notes")
    op.execute("DROP INDEX ix_messages_content_search")
    op.execute("DELETE FROM messages WHERE msg_type = 'event'")
    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.create_check_constraint("messages_msg_type", "messages", _types(OLD_TYPES))
    op.drop_index("ix_conversations_tenant_id_last_message_at", "conversations")
    for column in (
        "unread_count",
        "last_message_at",
        "closed_at",
        "takeover_activity_at",
        "takeover_started_at",
        "takeover_user_id",
    ):
        op.drop_column("conversations", column)
