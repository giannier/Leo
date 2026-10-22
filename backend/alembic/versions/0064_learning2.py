"""Chat imports, real examples and how the sellers write (017, phase 2).

Revision ID: 0064
Revises: 0063

* `chat_imports`: a WhatsApp chat the business handed over to learn from. Only the authorisation, the
  counts and the result are kept; the original is deleted when it is processed, also if it fails.
* `example_conversations`: real conversations, anonymised, the assistant consults when a similar case
  comes up (the "cuándo" of each is embedded like a situation's).
* `agent_configs.estilo_vendedor`: how the business's sellers write, approved by a person.
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064"
down_revision: str | Sequence[str] | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_imports",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="procesando", nullable=False),
        sa.Column("autorizado_por", sa.UUID(), nullable=True),
        sa.Column(
            "autorizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("mensajes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conversaciones", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audios", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audios_transcritos", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "minutos_audio", sa.Numeric(precision=8, scale=2), server_default="0", nullable=False
        ),
        sa.Column("sugerencias", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "creada_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("terminada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "estado IN ('procesando', 'listo', 'fallido')", name=op.f("ck_chat_imports_estado")
        ),
        sa.ForeignKeyConstraint(
            ["autorizado_por"],
            ["users.id"],
            name=op.f("fk_chat_imports_autorizado_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_chat_imports_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_imports")),
    )
    op.create_table(
        "example_conversations",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("tema", sa.Text(), nullable=False),
        sa.Column(
            "mensajes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("origen", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="activo", nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("sugerencia_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "estado IN ('activo', 'inactivo')", name=op.f("ck_example_conversations_estado")
        ),
        sa.CheckConstraint(
            "origen IN ('importacion', 'venta_confirmada')",
            name=op.f("ck_example_conversations_origen"),
        ),
        sa.CheckConstraint(
            "char_length(tema) BETWEEN 1 AND 300", name=op.f("ck_example_conversations_tema_length")
        ),
        sa.CheckConstraint(
            "char_length(titulo) BETWEEN 1 AND 80",
            name=op.f("ck_example_conversations_titulo_length"),
        ),
        sa.ForeignKeyConstraint(
            ["sugerencia_id"],
            ["suggestions.id"],
            name=op.f("fk_example_conversations_sugerencia_id_suggestions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_example_conversations_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_example_conversations")),
    )
    op.create_index(
        op.f("ix_example_conversations_tenant_id"),
        "example_conversations",
        ["tenant_id"],
        unique=False,
    )
    op.add_column("agent_configs", sa.Column("estilo_vendedor", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_configs", "estilo_vendedor")
    op.drop_index(op.f("ix_example_conversations_tenant_id"), table_name="example_conversations")
    op.drop_table("example_conversations")
    op.drop_table("chat_imports")
