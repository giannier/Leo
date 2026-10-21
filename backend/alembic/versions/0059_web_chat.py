"""The web chat: a channel that needs no WhatsApp number (feature 019, phase F).

Revision ID: 0059
Revises: 0058

A business puts a chat on its website, or shares its link from a social network's bio, and the assistant
answers there with the same agenda, quotes, orders and leads as on WhatsApp. A conversation of channel `web`
is a real one: it appears in the inbox, counts in the results and in the quota.

`web_chats` is the business's public chat: its public key (the address of the chat, rotated if it leaks), the
switch (off until the owner turns it on), a greeting and a colour, and a daily cap of conversations' messages
so a public endpoint cannot spend the business's budget.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | Sequence[str] | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNEL_CONSTRAINT = "ck_conversations_conversations_channel"


def upgrade() -> None:
    op.drop_constraint(op.f(CHANNEL_CONSTRAINT), "conversations", type_="check")
    op.create_check_constraint(
        op.f(CHANNEL_CONSTRAINT), "conversations", "channel IN ('sandbox', 'whatsapp', 'web')"
    )
    op.create_table(
        "web_chats",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("saludo", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), server_default="#2563eb", nullable=False),
        sa.Column("limite_diario", sa.Integer(), server_default="300", nullable=False),
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
            "color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_web_chats_web_chats_color")
        ),
        sa.CheckConstraint(
            "limite_diario BETWEEN 10 AND 5000", name=op.f("ck_web_chats_web_chats_limite_diario")
        ),
        sa.CheckConstraint(
            "saludo IS NULL OR char_length(saludo) <= 300",
            name=op.f("ck_web_chats_web_chats_saludo"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_web_chats_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_web_chats")),
        sa.UniqueConstraint("public_key", name=op.f("uq_web_chats_public_key")),
    )


def downgrade() -> None:
    op.drop_table("web_chats")
    op.execute("DELETE FROM conversations WHERE channel = 'web'")
    op.drop_constraint(op.f(CHANNEL_CONSTRAINT), "conversations", type_="check")
    op.create_check_constraint(
        op.f(CHANNEL_CONSTRAINT), "conversations", "channel IN ('sandbox', 'whatsapp')"
    )
