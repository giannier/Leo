"""Situations and the business profile by form (Feature 017, TASK-462 and TASK-434)

* `situations`: a business's own rules as cards (RF-006), with the embedding of
  "cuándo aplica" for the selector (C-10).
* `agent_configs`: `tratamiento`, `emojis`, `saludo`, `politica_descuentos`,
  `descuento_maximo_pct` and `max_situaciones` (RF-010, C-10).
* `payment_methods_business.titular`: the account holder a receipt is compared
  with (004 D-1).
* `agent_traces.situaciones`: which situations entered a turn, with their
  distance (TASK-463).
* Data: a non-empty `agent_configs.restrictions` becomes one situation (origin
  `migrada`, always on, every stage) and the column is dropped (RF-011). The
  text is kept whole: situations allow 4000 characters for migrated text.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_COLUMNS = (
    ("tratamiento", sa.Text(), "tu"),
    ("emojis", sa.Text(), "pocos"),
    ("saludo", sa.Text(), None),
    ("politica_descuentos", sa.Text(), "solo_promociones"),
    ("descuento_maximo_pct", sa.Numeric(5, 2), None),
    ("max_situaciones", sa.Integer(), "5"),
)
AGENT_CHECKS = (
    ("ck_agent_configs_tratamiento", "tratamiento IN ('tu', 'usted')"),
    ("ck_agent_configs_emojis", "emojis IN ('no', 'pocos', 'si')"),
    (
        "ck_agent_configs_politica_descuentos",
        "politica_descuentos IN ('no_ofrece', 'solo_promociones', 'negociable')",
    ),
    ("ck_agent_configs_saludo_length", "char_length(saludo) <= 120"),
    (
        "ck_agent_configs_descuento_maximo_pct",
        "descuento_maximo_pct IS NULL OR descuento_maximo_pct BETWEEN 0 AND 100",
    ),
    ("ck_agent_configs_max_situaciones", "max_situaciones BETWEEN 1 AND 10"),
)


def upgrade() -> None:
    op.create_table(
        "situations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("cuando_aplica", sa.Text(), nullable=False),
        sa.Column("que_hace", sa.Text(), nullable=False),
        sa.Column(
            "media_asset_ids", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False
        ),
        sa.Column("derivar", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ejemplo_respuesta", sa.Text(), nullable=True),
        sa.Column("etapas", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("siempre", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="borrador", nullable=False),
        sa.Column("origen", sa.Text(), server_default="manual", nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            "estado IN ('borrador', 'activa', 'inactiva')", name=op.f("ck_situations_estado")
        ),
        sa.CheckConstraint(
            "origen IN ('manual', 'sugerida_rubro', 'migrada', 'aprendida')",
            name=op.f("ck_situations_origen"),
        ),
        sa.CheckConstraint(
            "char_length(titulo) BETWEEN 1 AND 80", name=op.f("ck_situations_titulo_length")
        ),
        sa.CheckConstraint(
            "char_length(cuando_aplica) BETWEEN 1 AND 300",
            name=op.f("ck_situations_cuando_length"),
        ),
        sa.CheckConstraint(
            "char_length(que_hace) BETWEEN 1 AND 4000", name=op.f("ck_situations_que_hace_length")
        ),
        sa.CheckConstraint(
            "char_length(ejemplo_respuesta) <= 600", name=op.f("ck_situations_ejemplo_length")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_situations_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_situations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_situations")),
    )
    op.create_index(op.f("ix_situations_tenant_id"), "situations", ["tenant_id"])

    for name, type_, default in AGENT_COLUMNS:
        op.add_column(
            "agent_configs",
            sa.Column(name, type_, server_default=default, nullable=default is None),
        )
    for name, condition in AGENT_CHECKS:
        op.create_check_constraint(name, "agent_configs", condition)
    op.add_column("payment_methods_business", sa.Column("titular", sa.Text(), nullable=True))
    op.add_column(
        "agent_traces",
        sa.Column("situaciones", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.execute(
        """
        INSERT INTO situations (tenant_id, titulo, cuando_aplica, que_hace, siempre, estado, origen)
        SELECT tenant_id, 'Reglas anteriores del negocio',
               'Siempre: son las reglas que el negocio escribió antes de las situaciones.',
               left(btrim(restrictions), 4000), true, 'activa', 'migrada'
        FROM agent_configs
        WHERE btrim(coalesce(restrictions, '')) <> ''
        """
    )
    op.drop_column("agent_configs", "restrictions")


def downgrade() -> None:
    op.add_column("agent_configs", sa.Column("restrictions", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE agent_configs a SET restrictions = s.que_hace
        FROM situations s
        WHERE s.tenant_id = a.tenant_id AND s.origen = 'migrada'
        """
    )
    op.drop_column("agent_traces", "situaciones")
    op.drop_column("payment_methods_business", "titular")
    for name, _ in reversed(AGENT_CHECKS):
        op.drop_constraint(name, "agent_configs", type_="check")
    for name, _, _ in reversed(AGENT_COLUMNS):
        op.drop_column("agent_configs", name)
    op.drop_index(op.f("ix_situations_tenant_id"), table_name="situations")
    op.drop_table("situations")
