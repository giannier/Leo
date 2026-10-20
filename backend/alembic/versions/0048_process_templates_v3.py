"""Process templates v3: the assistant sounds like a seller.

Revision ID: 0048
Revises: 0047

A real conversation showed the assistant informing without selling: it listed prices, offered
"do you want me to tell you?" instead of telling, and never asked for a name or an email. The prompt
now carries a sales playbook (`agent/prompts/stage.py`), and the stage texts that lead the
conversation change here, as version 3 (`leo-sdd/features/004-motor-de-ventas/ventas.md`):

* IDENTIFICAR of *productos* helps the customer choose with one question and moves to the order.
* IDENTIFICAR of *servicios* asks one open question at a time, recommends one option and, for a quote,
  asks for a name and an email before handing over to an advisor with a summary.
* ASESORIA (the base text, used by *productos* and *servicios*) also asks how to reach the customer.

Published versions are frozen (017, C-04), so this is version 3 of each template: every rule of v2 with
the same `rule_key`s (a business that switched a rule off keeps it off) and the texts below. Businesses
see the change as news. *certificaciones* gets a version 3 identical to v2, so the three stay aligned.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | Sequence[str] | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = ("productos", "certificaciones", "servicios")
NOTE = "v3: el asistente sondea, recomienda con seguridad y cierra (ventas.md)"

# (template, stage) -> the new rules of that stage, in order. Same count as v2, so each keeps its key.
NEW_RULES: dict[tuple[str, str], list[str]] = {
    ("productos", "IDENTIFICAR"): [
        "Ayúdalo a elegir y llévalo al pedido.",
        "- Si sabe lo que quiere, búscalo con buscar_productos y confírmale precio y disponibilidad.",
        '- Si ya dijo qué productos quiere y cada uno coincide con uno solo, agrégalos en el mismo turno con\n  gestionar_pedido(accion="agregar", cantidad=la que dijo): no le preguntes si quiere que los agregues.',
        "- Si no sabe qué elegir, pregúntale para qué lo necesita (una pregunta), recomiéndale UNA opción por esa razón y muestra más con mostrar_opciones solo si las pide.",
        "- Envía la imagen del producto (enviar_recurso) solo si la pide o si ayuda a decidir.",
        '- Cuando elija, agrégalo con gestionar_pedido(accion="agregar") y propón el siguiente paso: cerrar el pedido, o un producto del catálogo que lo complemente si de verdad encaja.',
    ],
    ("productos", "ASESORIA"): [
        "El cliente eligió un camino que atiende un asesor.",
        'Si el ESTADO NO dice "asesoría ya derivada": haz máximo 2 preguntas para entender qué necesita, pídele su\nnombre y cómo contactarlo (para qué: que el asesor le escriba) y llama a\nderivar_a_humano(motivo="asesoria") con un resumen claro. Dile que un asesor le escribirá pronto.',
        'Si el ESTADO dice "asesoría ya derivada": responde sus dudas con consultar_conocimiento, no vuelvas a\nderivar salvo que pida hablar con alguien, y si muestra interés en otro camino, ofrécelo con\nmostrar_opciones(tipo="caminos") o regístralo con elegir_camino.',
    ],
    ("servicios", "IDENTIFICAR"): [
        "Entiende qué necesita antes de ofrecer.",
        "- Pregunta de a una, abierta y sobre lo último que dijo (qué quiere lograr, cómo lo resuelve hoy, para cuándo). Con 2 respuestas ya recomiendas; no hagas más de 3 preguntas en total.",
        "- Con lo que contó, busca con buscar_productos y recomienda UNA opción explicando por qué le sirve a su caso.",
        '- Si el servicio es de precio fijo y el cliente dice que lo quiere, lo contrata o muestra interés claro ("me interesa", "¿cómo seguimos?", "¿cómo pago?"), agrégalo en el mismo turno con gestionar_pedido: no le preguntes si quieres agregarlo. Si solo pregunta, explícalo y propón el siguiente paso.',
        '- Si es de cotización, o lo que necesita es a medida o junta varios servicios: resume en dos líneas lo que entendiste (qué necesita, plazo, presupuesto si lo dijo) y pídele su nombre y correo, explicando que es para que el asesor le envíe la propuesta, y espera su respuesta. Con su nombre y correo (o si prefiere no darlos) no pidas más datos ni uses registrar_datos_cliente: llama a derivar_a_humano(motivo="asesoria") con ese resumen, incluyendo esos datos, y dile que un asesor le escribirá pronto.',
    ],
    ("servicios", "ASESORIA"): [
        "El cliente eligió un camino que atiende un asesor.",
        'Si el ESTADO NO dice "asesoría ya derivada": haz máximo 2 preguntas para entender qué necesita, pídele su\nnombre y cómo contactarlo (para qué: que el asesor le escriba) y llama a\nderivar_a_humano(motivo="asesoria") con un resumen claro. Dile que un asesor le escribirá pronto.',
        'Si el ESTADO dice "asesoría ya derivada": responde sus dudas con consultar_conocimiento, no vuelvas a\nderivar salvo que pida hablar con alguien, y si muestra interés en otro camino, ofrécelo con\nmostrar_opciones(tipo="caminos") o regístralo con elegir_camino.',
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    for key in KEYS:
        v2 = bind.execute(
            sa.text(
                "SELECT id FROM catalog_versions WHERE kind = 'proceso' AND key = :key "
                "AND version = 2"
            ),
            {"key": key},
        ).scalar_one_or_none()
        if v2 is None:
            continue
        v3 = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES ('proceso', :key, 3, 'borrador', :notas) RETURNING id"
            ),
            {"key": key, "notas": NOTE},
        ).scalar_one()
        rules = bind.execute(
            sa.text(
                "SELECT rule_key, etapa, texto, orden FROM catalog_rules "
                "WHERE version_id = :v ORDER BY orden"
            ),
            {"v": v2},
        ).all()
        # The v2 rules of each changed stage, in order, take the new texts one by one.
        replaced: dict[str, str] = {}
        for (template, stage), texts in NEW_RULES.items():
            if template != key:
                continue
            old = [r for r in rules if r.etapa == stage]
            if len(old) != len(texts):
                raise RuntimeError(
                    f"{key}/{stage}: v2 has {len(old)} rules and v3 {len(texts)}; "
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
                    "v": v3,
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
            {"v": v3},
        )


def downgrade() -> None:
    # The rules of a published version are frozen by trigger; lifted for this one step.
    op.execute("ALTER TABLE catalog_rules DISABLE TRIGGER catalog_rules_frozen")
    op.execute(
        "DELETE FROM catalog_rules WHERE version_id IN (SELECT id FROM catalog_versions "
        "WHERE kind = 'proceso' AND version = 3)"
    )
    op.execute(
        "UPDATE tenant_catalog_seen SET version_vista = 2 "
        "WHERE kind = 'proceso' AND version_vista = 3"
    )
    op.execute("DELETE FROM catalog_versions WHERE kind = 'proceso' AND version = 3")
    op.execute("ALTER TABLE catalog_rules ENABLE TRIGGER catalog_rules_frozen")
