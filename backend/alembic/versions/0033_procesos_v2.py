"""Process templates v2: CALIFICAR and ANUNCIO never hold an answer back for the name.

Revision ID: 0033
Revises: 0032

The sales-flow evals (TASK-447) found the assistant answering "before I tell you
the price, what's your name?" and ignoring a path the customer had already stated
("solo certificar, quiero el paquete de 5"): the v1 rule said "if you do not know
their name, ask for it and stop". v2 keeps asking for the name, but records the
stated path first and answers the question. The acceptance walk (TASK-449) found the
same in ANUNCIO: a customer from an ad got only "what's your name?".

Published versions are frozen (017, C-04), so this is version 2 of each template:
every rule of v1, with the same `rule_key`s (a business that switched a rule off
keeps it off), and the changed texts below. Businesses see the change as news.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = ("productos", "certificaciones", "servicios")
CHANGED = {
    "IDENTIFICAR.cuando_elija_llama_gestionar_pedido": (
        "Cuando elija, o si ya dijo antes qué paquete quiere (no le muestres los paquetes otra "
        'vez), llama a gestionar_pedido(accion="agregar") con el paquete y las selecciones que '
        "ya dijo."
    ),
    "IDENTIFICAR.servicio_es_precio_fijo_explicalo": (
        "- Si el servicio es de precio fijo y el cliente dice que lo quiere o lo contrata, agrégalo "
        "en el mismo turno con gestionar_pedido: no le preguntes si quieres agregarlo. Si solo "
        "pregunta, explícalo."
    ),
    "ANUNCIO.no_sabes_su_nombre_pidelo": (
        "- Si no sabes su nombre, pídelo, pero en el mismo mensaje ve a lo anunciado: nunca "
        "condiciones la información a que te dé su nombre."
    ),
    "CALIFICAR.aun_no_sabes_su_nombre": (
        "- Si aún no sabes su nombre, pídelo. Si en ese mismo mensaje ya dijo qué busca o hizo "
        "una pregunta, primero registra el camino con elegir_camino y responde su pregunta: "
        "nunca condiciones una respuesta a que te dé su nombre."
    ),
}
NOTE = (
    "v2 (TASK-447/449): CALIFICAR y ANUNCIO responden y registran el camino aunque falte el nombre"
)


def upgrade() -> None:
    bind = op.get_bind()
    for key in KEYS:
        v1 = bind.execute(
            sa.text(
                "SELECT id FROM catalog_versions WHERE kind = 'proceso' AND key = :key "
                "AND version = 1"
            ),
            {"key": key},
        ).scalar_one_or_none()
        if v1 is None:
            continue
        v2 = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES ('proceso', :key, 2, 'borrador', :notas) RETURNING id"
            ),
            {"key": key, "notas": NOTE},
        ).scalar_one()
        rules = bind.execute(
            sa.text(
                "SELECT rule_key, etapa, texto, orden FROM catalog_rules "
                "WHERE version_id = :v ORDER BY orden"
            ),
            {"v": v1},
        ).all()
        bind.execute(
            sa.text(
                "INSERT INTO catalog_rules (version_id, orden, rule_key, etapa, texto) "
                "VALUES (:v, :orden, :rule_key, :etapa, :texto)"
            ),
            [
                {
                    "v": v2,
                    "orden": rule.orden,
                    "rule_key": rule.rule_key,
                    "etapa": rule.etapa,
                    "texto": CHANGED.get(rule.rule_key, rule.texto),
                }
                for rule in rules
            ],
        )
        bind.execute(
            sa.text(
                "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": v2},
        )


def downgrade() -> None:
    # The rules of a published version are frozen by trigger; lifted for this one step.
    op.execute("ALTER TABLE catalog_rules DISABLE TRIGGER catalog_rules_frozen")
    op.execute(
        "DELETE FROM catalog_rules WHERE version_id IN (SELECT id FROM catalog_versions "
        "WHERE kind = 'proceso' AND version = 2)"
    )
    op.execute(
        "UPDATE tenant_catalog_seen SET version_vista = 1 "
        "WHERE kind = 'proceso' AND version_vista = 2"
    )
    op.execute("DELETE FROM catalog_versions WHERE kind = 'proceso' AND version = 2")
    op.execute("ALTER TABLE catalog_rules ENABLE TRIGGER catalog_rules_frozen")
