"""messages.created_at: clock_timestamp(), not now()

PostgreSQL freezes `now()` at the start of the transaction. Two messages
written inside one - a customer's question and the agent's answer, or a burst
applied together - therefore carry the *same* `created_at`, and an
`ORDER BY created_at` over them returns whatever the planner felt like.

That order is not cosmetic. It is the conversation history the agent is given,
the definition of "everything since the last reply" the burst logic uses, and
the transcript the owner reads in the inbox. A tie there is an agent answering
the wrong question.

`clock_timestamp()` advances within a transaction, which is what an
append-only log actually means. Existing rows keep the timestamps they have;
only new inserts change.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "messages",
        "created_at",
        server_default=sa.text("clock_timestamp()"),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "messages",
        "created_at",
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
