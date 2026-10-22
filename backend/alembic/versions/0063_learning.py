"""The agent learns and a person decides (017 phases 3 and 4, 019 phase H).

Revision ID: 0063
Revises: 0062

* `suggestions`: what the assistant proposes to learn (a situation, an example, a fact, a style),
  waiting for the reviewer; a rejected one stays, so it is not proposed again.
* `config_history`: every change to the situations, the rule switches and the profile, with what it
  was before, so it can be undone.
* `unanswered_questions`: questions the knowledge could not answer, counted without any model call.
* `learning_settings` / `learning_usage`: off until the owner turns it on, with a weekly ceiling of
  model calls; sharing the text of a situation with the rubro is a separate, opt-in switch.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063"
down_revision: str | Sequence[str] | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tope_semanal", sa.Integer(), server_default="20", nullable=False),
        sa.Column("compartir_con_rubro", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("compartir_por", sa.UUID(), nullable=True),
        sa.Column("compartir_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "tope_semanal BETWEEN 0 AND 500", name=op.f("ck_learning_settings_tope_range")
        ),
        sa.ForeignKeyConstraint(
            ["compartir_por"],
            ["users.id"],
            name=op.f("fk_learning_settings_compartir_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_learning_settings_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_learning_settings")),
    )
    op.create_table(
        "learning_usage",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("semana", sa.Date(), nullable=False),
        sa.Column("llamadas", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "minutos_audio", sa.Numeric(precision=8, scale=2), server_default="0", nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_learning_usage_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_usage")),
        sa.UniqueConstraint("tenant_id", "semana", name="uq_learning_usage_tenant_semana"),
    )
    op.create_table(
        "suggestions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="pendiente", nullable=False),
        sa.Column("resumen", sa.Text(), nullable=False),
        sa.Column(
            "propuesta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "conversaciones", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False
        ),
        sa.Column(
            "choques",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("huella", sa.Text(), nullable=False),
        sa.Column("decidida_por", sa.UUID(), nullable=True),
        sa.Column("decidida_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column(
            "aplicada",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
            "estado IN ('pendiente', 'aprobada', 'rechazada')", name=op.f("ck_suggestions_estado")
        ),
        sa.CheckConstraint(
            "fuente IN ('toma_de_control', 'venta_confirmada', 'venta_perdida', 'pregunta_sin_respuesta', 'importacion')",
            name=op.f("ck_suggestions_fuente"),
        ),
        sa.CheckConstraint(
            "tipo IN ('situacion', 'ejemplo', 'dato', 'estilo')", name=op.f("ck_suggestions_tipo")
        ),
        sa.ForeignKeyConstraint(
            ["decidida_por"],
            ["users.id"],
            name=op.f("fk_suggestions_decidida_por_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_suggestions_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suggestions")),
        sa.UniqueConstraint("tenant_id", "huella", name="uq_suggestions_tenant_id_huella"),
    )
    op.create_index(
        "ix_suggestions_tenant_id_estado",
        "suggestions",
        ["tenant_id", "estado", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "unanswered_questions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("pregunta", sa.Text(), nullable=False),
        sa.Column("huella", sa.Text(), nullable=False),
        sa.Column("veces", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "conversaciones", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False
        ),
        sa.Column("estado", sa.Text(), server_default="abierta", nullable=False),
        sa.Column(
            "primera_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "ultima_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "estado IN ('abierta', 'sugerida', 'descartada')",
            name=op.f("ck_unanswered_questions_estado"),
        ),
        sa.CheckConstraint(
            "char_length(pregunta) <= 200", name=op.f("ck_unanswered_questions_pregunta_length")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_unanswered_questions_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unanswered_questions")),
        sa.UniqueConstraint("tenant_id", "huella", name="uq_unanswered_questions_tenant_id_huella"),
    )
    op.create_table(
        "config_history",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("entidad", sa.Text(), nullable=False),
        sa.Column("entidad_id", sa.UUID(), nullable=True),
        sa.Column("accion", sa.Text(), nullable=False),
        sa.Column("resumen", sa.Text(), nullable=False),
        sa.Column("antes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("despues", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actor", sa.UUID(), nullable=True),
        sa.Column("sugerencia_id", sa.UUID(), nullable=True),
        sa.Column("deshecho_por", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "accion IN ('crear', 'editar', 'estado', 'borrar', 'deshacer')",
            name=op.f("ck_config_history_accion"),
        ),
        sa.CheckConstraint(
            "entidad IN ('situacion', 'interruptor', 'perfil', 'faq', 'ejemplo', 'estilo')",
            name=op.f("ck_config_history_entidad"),
        ),
        sa.ForeignKeyConstraint(
            ["actor"], ["users.id"], name=op.f("fk_config_history_actor_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["deshecho_por"],
            ["config_history.id"],
            name=op.f("fk_config_history_deshecho_por_config_history"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sugerencia_id"],
            ["suggestions.id"],
            name=op.f("fk_config_history_sugerencia_id_suggestions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_config_history_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_history")),
    )
    op.create_index(
        "ix_config_history_tenant_id_created_at",
        "config_history",
        ["tenant_id", sa.literal_column("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_config_history_tenant_id_created_at", table_name="config_history")
    op.drop_table("config_history")
    op.drop_table("unanswered_questions")
    op.drop_index("ix_suggestions_tenant_id_estado", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_table("learning_usage")
    op.drop_table("learning_settings")
