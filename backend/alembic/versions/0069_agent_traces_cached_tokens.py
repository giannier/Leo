"""How much of each turn's prompt came from the provider's cache (agent_traces.cached_input_tokens).

Revision ID: 0069
Revises: 0068

Cached input is billed at a fraction (OpenAI: 75 % off on gpt-4.1-mini, 90 % on gpt-5.4-mini), and
the prompt is now laid out so the part that does not change, and the conversation so far, come first
(2026-10-27). Without the count, a turn's cost reads as if nothing were cached.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_traces",
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_traces", "cached_input_tokens")
