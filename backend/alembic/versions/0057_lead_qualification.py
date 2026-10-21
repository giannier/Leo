"""Capture and qualify leads (feature 019, phase E).

Revision ID: 0057
Revises: 0056

* `rubros.preguntas_calificacion`: the questions agents of that rubro usually ask to know a customer (three
  or four, one at a time, never an interrogation). `agent_configs.preguntas_calificacion` is the owner's own
  list; NULL means "use the rubro's".
* `leads.score*`: how warm the lead is (`caliente`, `tibio`, `frio`), why, and what the customer answered.
  The assistant judges it with the rubric in its prompt (a concrete need and a short deadline, or a request
  for price, appointment or proposal, is hot) and a person can read the answers behind it.
* a `lead_caliente` notice, so the team hears about a hot lead while it is still hot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057"
down_revision: str | Sequence[str] | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_BEFORE = (
    "trial_ending_3d",
    "trial_ending_1d",
    "trial_ended",
    "wizard_reminder",
    "quota_80",
    "nuevo_comprobante",
    "pedido_contra_entrega",
    "derivacion",
    "ventana_cerrada",
    "llm_caido",
    "derivacion_sin_atender",
)
KINDS_AFTER = (*KINDS_BEFORE, "lead_caliente")
CONSTRAINT = "ck_tenant_notifications_tenant_notifications_kind"

QUESTIONS: dict[str, list[str]] = {
    "restaurante": [
        "¿Para cuántas personas es?",
        "¿Para qué día y hora lo necesitas?",
        "¿Es para el local, para llevar o delivery?",
    ],
    "instituto": [
        "¿Qué curso o certificación te interesa?",
        "¿Para cuándo lo necesitas?",
        "¿Es para ti o para tu empresa?",
    ],
    "retail": [
        "¿Qué estás buscando?",
        "¿Para cuándo lo necesitas?",
        "¿Tienes un presupuesto en mente?",
    ],
    "servicios": [
        "¿Qué necesitas resolver?",
        "¿Para cuándo lo necesitas?",
        "¿Tienes un presupuesto aproximado?",
    ],
    "salud": [
        "¿Qué especialidad o servicio buscas?",
        "¿Para cuándo quisieras la cita?",
        "¿Es tu primera vez con nosotros?",
    ],
    "tecnologia": [
        "¿Qué proceso o problema quieres resolver?",
        "¿Cuántas personas lo usarían?",
        "¿Para cuándo lo necesitas?",
        "¿Tienes un presupuesto aproximado?",
    ],
    "belleza": [
        "¿Qué servicio te gustaría?",
        "¿Para qué día quieres venir?",
        "¿Es tu primera vez con nosotros?",
    ],
    "inmobiliaria": [
        "¿Buscas comprar o alquilar?",
        "¿En qué zona?",
        "¿Cuál es tu presupuesto?",
        "¿Para cuándo lo necesitas?",
    ],
    "automotriz": [
        "¿Qué modelo o tipo de vehículo buscas?",
        "¿Cuál es tu presupuesto?",
        "¿Lo pensarías al contado o financiado?",
        "¿Para cuándo quieres decidir?",
    ],
    "hogar": [
        "¿Qué necesitas arreglar o instalar?",
        "¿En qué distrito estás?",
        "¿Es urgente o puede esperar unos días?",
    ],
    "turismo": ["¿A dónde quieres ir?", "¿En qué fechas?", "¿Cuántas personas viajan?"],
    "b2b": [
        "¿Qué productos necesitas?",
        "¿Qué cantidad aproximada por pedido?",
        "¿Con qué frecuencia compras?",
    ],
    "otros": [
        "¿Qué estás buscando?",
        "¿Para cuándo lo necesitas?",
        "¿Tienes un presupuesto aproximado?",
    ],
}


def _in(values: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column(
        "rubros",
        sa.Column(
            "preguntas_calificacion",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    bind = op.get_bind()
    for key, questions in QUESTIONS.items():
        bind.execute(
            sa.text("UPDATE rubros SET preguntas_calificacion = :q WHERE key = :k"),
            {"q": questions, "k": key},
        )
    op.add_column(
        "agent_configs",
        sa.Column("preguntas_calificacion", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_agent_configs_agent_configs_preguntas_calificacion"),
        "agent_configs",
        "preguntas_calificacion IS NULL OR cardinality(preguntas_calificacion) <= 5",
    )
    op.add_column("leads", sa.Column("score", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("score_reason", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "qualification",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("leads", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_leads_leads_score"),
        "leads",
        "score IS NULL OR score IN ('caliente', 'tibio', 'frio')",
    )
    op.create_index("ix_leads_tenant_id_score", "leads", ["tenant_id", "score"])
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "tenant_notifications", _in(KINDS_AFTER))


def downgrade() -> None:
    op.execute("DELETE FROM tenant_notifications WHERE kind = 'lead_caliente'")
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "tenant_notifications", _in(KINDS_BEFORE))
    op.drop_index("ix_leads_tenant_id_score", table_name="leads")
    op.drop_constraint(op.f("ck_leads_leads_score"), "leads", type_="check")
    for column in ("scored_at", "qualification", "score_reason", "score"):
        op.drop_column("leads", column)
    op.drop_constraint(
        op.f("ck_agent_configs_agent_configs_preguntas_calificacion"),
        "agent_configs",
        type_="check",
    )
    op.drop_column("agent_configs", "preguntas_calificacion")
    op.drop_column("rubros", "preguntas_calificacion")
