# ruff: noqa: E501
"""Certificaciones for a literal model: add what the customer already chose (process templates v5).

Revision ID: 0068
Revises: 0067

gpt-4.1-mini, which follows instructions literally, failed the certificaciones flows (5 of 8,
2026-10-27): told «Paso 1 · el tema, Paso 2 · el precio por unidad, Paso 3 · los paquetes», it walked
the three steps with a customer who had already said «quiero el paquete de 5 con SIAF y Excel», and
added nothing; and it copied the sample phrase «¡Buenísimo! Ya tienes [X]…» word for word. Version 5:

* IDENTIFICAR: first, if the customer already said which certification or package, add it now with
  gestionar_pedido and the themes said, without asking for personal data first; the unit price
  («el ancla») only if they have not chosen yet;
* PEDIDO: say how many they have and how many are missing in your own words.

Same method as 0067: version 5 of each template, every rule of v4 with the same `rule_key`, the texts
below for the changed ones; *productos* and *servicios* get a v5 identical to v4.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: str | Sequence[str] | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = ("productos", "certificaciones", "servicios")
NOTE = "v5: certificaciones agrega lo que el cliente ya eligió; sin frases de ejemplo"

NEW_RULES: dict[tuple[str, str], list[str]] = {
    ("certificaciones", "IDENTIFICAR"): [
        'Si ya dijo qué certificación o paquete quiere, agrégalo ya con gestionar_pedido(accion="agregar") y los temas que dijo, sin pedir datos antes. Si no, Paso 1 · El tema: pregúntale qué tema necesita certificar.',
        "Cuando lo diga, búscalo con buscar_productos. La respuesta es SIEMPRE SÍ: se certifica cualquier tema.",
        'Si no aparece en el catálogo, confírmalo con el nombre que escribió el cliente. Prohibido decir "no lo\ntenemos" o derivar por este motivo. Si menciona varios temas, confírmalos todos juntos en una lista.',
        "Si no sabe qué pedir, muéstrale categorías con mostrar_opciones.\n",
        "Paso 2 · El ancla. Si aún no eligió, antes del precio del paquete menciona el precio por unidad del certificado.\n",
        'Paso 3 · Los paquetes. Muéstralos con mostrar_opciones(tipo="productos", filtro="paquetes"), envía el recurso\ndel paquete destacado y empuja el destacado en un mensaje corto. Copia los precios tal cual.',
        'Cuando elija, o si ya dijo antes qué paquete quiere (no le muestres los paquetes otra vez), llama a gestionar_pedido(accion="agregar") con el paquete y las selecciones que ya dijo.',
    ],
    ("certificaciones", "PEDIDO"): [
        "El paquete aún no tiene todas sus certificaciones (el ESTADO dice cuántas faltan). No avances a los datos. Dile con tus palabras cuántas tiene y cuántas le faltan, y pregúntale qué otros temas le sirven.",
        'Cada tema nuevo: gestionar_pedido(accion="elegir", seleccion="<tema>"). Si no se le ocurren, muéstrale\ncategorías con mostrar_opciones. Si quiere cambiar de paquete, quita el actual y agrega el nuevo.',
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    for key in KEYS:
        v4_id = bind.execute(
            sa.text(
                "SELECT id FROM catalog_versions WHERE kind = 'proceso' AND key = :key "
                "AND version = 4"
            ),
            {"key": key},
        ).scalar_one_or_none()
        if v4_id is None:
            continue
        v5 = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES ('proceso', :key, 5, 'borrador', :notas) RETURNING id"
            ),
            {"key": key, "notas": NOTE},
        ).scalar_one()
        rules = bind.execute(
            sa.text(
                "SELECT rule_key, etapa, texto, orden FROM catalog_rules "
                "WHERE version_id = :v ORDER BY orden"
            ),
            {"v": v4_id},
        ).all()
        # The v4 rules of each changed stage, in order, take the new texts one by one.
        replaced: dict[str, str] = {}
        for (template, stage), texts in NEW_RULES.items():
            if template != key:
                continue
            old = [r for r in rules if r.etapa == stage]
            if len(old) != len(texts):
                raise RuntimeError(
                    f"{key}/{stage}: v4 has {len(old)} rules and v5 {len(texts)}; "
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
                    "v": v5,
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
            {"v": v5},
        )


def downgrade() -> None:
    # The rules of a published version are frozen by trigger; lifted for this one step.
    op.execute("ALTER TABLE catalog_rules DISABLE TRIGGER catalog_rules_frozen")
    op.execute(
        "DELETE FROM catalog_rules WHERE version_id IN (SELECT id FROM catalog_versions "
        "WHERE kind = 'proceso' AND version = 5)"
    )
    op.execute(
        "UPDATE tenant_catalog_seen SET version_vista = 4 "
        "WHERE kind = 'proceso' AND version_vista = 5"
    )
    op.execute("DELETE FROM catalog_versions WHERE kind = 'proceso' AND version = 5")
    op.execute("ALTER TABLE catalog_rules ENABLE TRIGGER catalog_rules_frozen")
