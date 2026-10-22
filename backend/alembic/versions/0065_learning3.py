"""The conversations the learning already looked at (017, phase 3).

Revision ID: 0065
Revises: 0064

`learning_marks`: one line per conversation and source, so a conversation is analysed once (and paid
for once), whether it gave a suggestion or not.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | Sequence[str] | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_marks",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fuente IN ('toma_de_control', 'venta_confirmada', 'venta_perdida', 'pregunta_sin_respuesta', 'importacion')",
            name=op.f("ck_learning_marks_fuente"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_learning_marks_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_learning_marks_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_marks")),
        sa.UniqueConstraint(
            "tenant_id", "conversation_id", "fuente", name="uq_learning_marks_tenant_conv_source"
        ),
    )


def downgrade() -> None:
    op.drop_table("learning_marks")
