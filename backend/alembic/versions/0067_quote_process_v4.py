"""Quotes: one way to gather the data, stated once (process templates v4).

Revision ID: 0067
Revises: 0066

A real conversation showed the assistant quoting badly (`evals/real_conversations.py`): it said
«Sí, contamos con…» in every message, asked again for what the customer had just said, justified a
quote with a sales pitch nothing backed, and closed differently from one run to the next. Three
instructions pulled in different directions: the sales playbook (recommend ONE option and say why),
the *servicios* stage text (ask for a name and an email, then hand over) and the hint inside
`buscar_productos` (ask for what is missing from the product's list).

The prompt now carries a COTIZACIÓN block and a tool that keeps what the customer gave
(`anotar_cotizacion`); here the *servicios* IDENTIFICAR text stops contradicting it, as version 4:

* before asking, look at what the customer already said; never repeat a question;
* recommend only if the customer has not chosen yet, and only with the business's facts;
* a quote whose product asks for data follows the COTIZACIÓN block (it goes to a person by itself
  when nothing is missing); a product that asks for nothing keeps the old advisor path.

Published versions are frozen (017, C-04): version 4 of each template, every rule of v3 with the same
`rule_key` (a business that switched a rule off keeps it off) and the texts below. *productos* and
*certificaciones* get a v4 identical to v3 so the three stay aligned. Businesses see it as news.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | Sequence[str] | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = ("productos", "certificaciones", "servicios")
NOTE = "v4: la cotización junta los datos una vez y pasa sola a una persona"

NEW_RULES: dict[tuple[str, str], list[str]] = {
    ("servicios", "IDENTIFICAR"): [
        "Entiende qué necesita antes de ofrecer.",
        "- Antes de preguntar repasa lo que ya dijo: no preguntes lo que ya contó ni repitas una pregunta. Pregunta de a una y sobre lo último que dijo. Con 2 respuestas ya recomiendas; no hagas más de 3 preguntas en total (una cotización sigue el bloque COTIZACIÓN).",
        "- Con lo que contó, busca con buscar_productos y, si aún no eligió, recomienda UNA opción explicando por qué le sirve a su caso.",
        '- Si el servicio es de precio fijo y el cliente dice que lo quiere, lo contrata o muestra interés claro ("me interesa", "¿cómo seguimos?", "¿cómo pago?"), agrégalo en el mismo turno con gestionar_pedido: no le preguntes si quieres agregarlo. Si solo pregunta, explícalo y propón el siguiente paso.',
        '- Si es de cotización y el producto pide datos: sigue el bloque COTIZACIÓN. Si no pide datos, o lo que necesita es a medida o junta varios servicios: resume en dos líneas lo que entendiste (qué necesita, plazo, presupuesto si lo dijo) y pídele su nombre y correo, explicando que es para que el asesor le envíe la propuesta, y espera su respuesta. Con su nombre y correo (o si prefiere no darlos) no pidas más datos ni uses registrar_datos_cliente: llama a derivar_a_humano(motivo="asesoria") con ese resumen, incluyendo esos datos, y dile que un asesor le escribirá pronto.',
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    for key in KEYS:
        v3 = bind.execute(
            sa.text(
                "SELECT id FROM catalog_versions WHERE kind = 'proceso' AND key = :key "
                "AND version = 3"
            ),
            {"key": key},
        ).scalar_one_or_none()
        if v3 is None:
            continue
        v4 = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES ('proceso', :key, 4, 'borrador', :notas) RETURNING id"
            ),
            {"key": key, "notas": NOTE},
        ).scalar_one()
        rules = bind.execute(
            sa.text(
                "SELECT rule_key, etapa, texto, orden FROM catalog_rules "
                "WHERE version_id = :v ORDER BY orden"
            ),
            {"v": v3},
        ).all()
        # The v3 rules of each changed stage, in order, take the new texts one by one.
        replaced: dict[str, str] = {}
        for (template, stage), texts in NEW_RULES.items():
            if template != key:
                continue
            old = [r for r in rules if r.etapa == stage]
            if len(old) != len(texts):
                raise RuntimeError(
                    f"{key}/{stage}: v3 has {len(old)} rules and v4 {len(texts)}; "
                    "the keys would no longer line up"
                )
            replaced.update({rule.rule_key: text for rule, text in zip(old, texts, strict=True)})
        bind.execute(
            sa.text(
                "INSERT INTO catalog_rules (version_id, orden, rule_key, etapa, texto) "
                "VALUES (:v, :orden, :rule_key, :etapa, :texto)"
            ),
            [
                {
                    "v": v4,
                    "orden": rule.orden,
                    "rule_key": rule.rule_key,
                    "etapa": rule.etapa,
                    "texto": replaced.get(rule.rule_key, rule.texto),
                }
                for rule in rules
            ],
        )
        bind.execute(
            sa.text(
                "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": v4},
        )


def downgrade() -> None:
    # The rules of a published version are frozen by trigger; lifted for this one step.
    op.execute("ALTER TABLE catalog_rules DISABLE TRIGGER catalog_rules_frozen")
    op.execute(
        "DELETE FROM catalog_rules WHERE version_id IN (SELECT id FROM catalog_versions "
        "WHERE kind = 'proceso' AND version = 4)"
    )
    op.execute(
        "UPDATE tenant_catalog_seen SET version_vista = 3 "
        "WHERE kind = 'proceso' AND version_vista = 4"
    )
    op.execute("DELETE FROM catalog_versions WHERE kind = 'proceso' AND version = 4")
    op.execute("ALTER TABLE catalog_rules ENABLE TRIGGER catalog_rules_frozen")
