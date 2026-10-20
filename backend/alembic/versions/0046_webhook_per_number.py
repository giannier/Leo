"""A webhook address per WhatsApp number

Each business brings its own YCloud account, and each account signs its
deliveries with its own secret, so the single platform-wide webhook secret can
no longer verify them. Every number gets an unguessable token that lives in the
URL registered at YCloud (`/webhooks/ycloud/<token>`); the secret itself is kept
encrypted next to the number's other credentials.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("whatsapp_numbers", sa.Column("webhook_token", sa.Text(), nullable=True))
    # Numbers that already exist get a token too; 64 hex characters from two UUIDs.
    op.execute(
        "UPDATE whatsapp_numbers SET webhook_token = "
        "replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '')"
    )
    op.alter_column("whatsapp_numbers", "webhook_token", nullable=False)
    op.create_unique_constraint(
        "uq_whatsapp_numbers_webhook_token", "whatsapp_numbers", ["webhook_token"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_whatsapp_numbers_webhook_token", "whatsapp_numbers", type_="unique")
    op.drop_column("whatsapp_numbers", "webhook_token")
