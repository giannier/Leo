"""Where customers come from, a weekly review of real conversations, and who gets the hot leads (019).

Revision ID: 0060
Revises: 0059

* `contacts.origen`: where the customer came from ("Instagram", "TikTok", "Chat web"...), from the link they
  followed (`?o=` of the web chat) or from what they said ("te escribo desde Instagram"). A lead takes it.
* `conversation_reviews`: the owner rates a sample of the week's real conversations (bien, regular, mal) and
  says why: the quality loop (feature 019, phase C) that needs no model call.
* `agent_configs.destino_leads`: the person a hot lead is assigned to (and told), instead of nobody.
* `rubros.datos_a_pedir_sugeridos`: what the agents of that rubro usually ask before handing a quote over,
  offered when the owner writes the questions of a quoted service.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060"
down_revision: str | Sequence[str] | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUOTE_QUESTIONS: dict[str, list[str]] = {
    "tecnologia": [
        "Qué proceso quieres resolver",
        "Para cuántas personas",
        "Para cuándo lo necesitas",
    ],
    "servicios": ["Qué necesitas resolver", "Para cuándo lo necesitas", "Presupuesto aproximado"],
    "inmobiliaria": ["Zona que te interesa", "Presupuesto", "Para cuándo lo necesitas"],
    "automotriz": ["Modelo o tipo de vehículo", "Presupuesto", "Al contado o financiado"],
    "hogar": ["Qué necesitas arreglar o instalar", "Distrito", "Es urgente o puede esperar"],
    "turismo": ["Destino", "Fechas", "Número de personas"],
    "b2b": ["Productos que necesitas", "Cantidad por pedido", "Frecuencia de compra"],
    "salud": ["Qué servicio buscas", "Para cuándo", "Es tu primera vez con nosotros"],
    "instituto": ["Curso o certificación", "Para cuándo", "Para ti o para tu empresa"],
    "belleza": ["Servicio que quieres", "Para qué día", "Es tu primera vez"],
    "retail": ["Qué producto buscas", "Cantidad", "Para cuándo lo necesitas"],
    "restaurante": ["Para cuántas personas", "Día y hora", "Local, para llevar o delivery"],
    "otros": ["Qué necesitas", "Para cuándo", "Presupuesto aproximado"],
}


def upgrade() -> None:
    op.add_column("contacts", sa.Column("origen", sa.Text(), nullable=True))
    op.create_table(
        "conversation_reviews",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column(
            "motivos",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("nota", sa.Text(), nullable=True),
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
            "rating IN (-1, 0, 1)", name=op.f("ck_conversation_reviews_conversation_reviews_rating")
        ),
        sa.CheckConstraint(
            "nota IS NULL OR char_length(nota) <= 500",
            name=op.f("ck_conversation_reviews_conversation_reviews_nota"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_conversation_reviews_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_reviews_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_conversation_reviews_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_reviews")),
        sa.UniqueConstraint(
            "conversation_id", name=op.f("uq_conversation_reviews_conversation_id")
        ),
    )
    op.create_index(
        "ix_conversation_reviews_tenant_id_created_at",
        "conversation_reviews",
        ["tenant_id", "created_at"],
    )
    op.add_column("agent_configs", sa.Column("destino_leads", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_agent_configs_destino_leads_users"),
        "agent_configs",
        "users",
        ["destino_leads"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "rubros",
        sa.Column(
            "datos_a_pedir_sugeridos",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    bind = op.get_bind()
    for key, questions in QUOTE_QUESTIONS.items():
        bind.execute(
            sa.text("UPDATE rubros SET datos_a_pedir_sugeridos = :q WHERE key = :k"),
            {"q": questions, "k": key},
        )


def downgrade() -> None:
    op.drop_column("rubros", "datos_a_pedir_sugeridos")
    op.drop_constraint(
        op.f("fk_agent_configs_destino_leads_users"), "agent_configs", type_="foreignkey"
    )
    op.drop_column("agent_configs", "destino_leads")
    op.drop_table("conversation_reviews")
    op.drop_column("contacts", "origen")
