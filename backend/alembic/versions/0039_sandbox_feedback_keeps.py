"""A simulator rating outlives "Reiniciar chat" (Feature 018, TASK-1804)

Resetting the simulator deletes its messages. A rating is what the learning suggestions will start from
(017, Phase 3), so it keeps a copy of what the customer asked and what the assistant answered, and its
links to the message and the conversation become NULL instead of taking the rating with them.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FKS = (
    ("fk_sandbox_feedback_message_id_messages", "messages", "message_id"),
    ("fk_sandbox_feedback_conversation_id_conversations", "conversations", "conversation_id"),
)


def upgrade() -> None:
    op.add_column("sandbox_feedback", sa.Column("pregunta", sa.Text(), nullable=True))
    op.add_column(
        "sandbox_feedback",
        sa.Column("respuesta", sa.Text(), nullable=False, server_default=""),
    )
    for name, table, column in _FKS:
        op.drop_constraint(name, "sandbox_feedback", type_="foreignkey")
        op.alter_column("sandbox_feedback", column, nullable=True)
        op.create_foreign_key(
            name, "sandbox_feedback", table, [column], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    op.execute("DELETE FROM sandbox_feedback WHERE message_id IS NULL OR conversation_id IS NULL")
    for name, table, column in _FKS:
        op.drop_constraint(name, "sandbox_feedback", type_="foreignkey")
        op.alter_column("sandbox_feedback", column, nullable=False)
        op.create_foreign_key(name, "sandbox_feedback", table, [column], ["id"], ondelete="CASCADE")
    op.drop_column("sandbox_feedback", "respuesta")
    op.drop_column("sandbox_feedback", "pregunta")
