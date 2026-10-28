"""The template of the education rubro, with certifications in it (feature 020, phase F3).

Revision ID: 0073
Revises: 0072

Giannier, 2026-10-28: «certificaciones es parte del proceso de educación, así que va en la plantilla,
no es la definición general». What the `certificaciones` process template carried as platform code
(the path «certificar o estudiar», the customer data, the price of one certificate before the
packages, «se certifica cualquier tema», the categories for whoever does not know, the name first) is
here what the education template **suggests**: a business applies it and changes it.

Version 2 of the rubro `instituto`: every rule, question and objection of version 1 as they were (a
business that switched a rule off keeps it off), its four suggested situations with a key, five new
ones, and the template's paths, customer data and payment (`catalog_versions.plantilla`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | Sequence[str] | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUBRO = "instituto"
NOTE = "v2: plantilla de educación (feature 020): caminos, datos del cliente y certificaciones"

PLANTILLA = {
    "calificacion": {
        "activa": True,
        "pregunta": (
            "¿Quieres certificar la experiencia que ya tienes o estudiar un programa completo?"
        ),
        "caminos": [
            {
                "clave": "certificacion",
                "titulo": "Solo certificar",
                "tipo": "venta",
                "descripcion": "Ya tiene la experiencia",
            },
            {
                "clave": "programa",
                "titulo": "Programa completo",
                "tipo": "asesoria",
                "descripcion": "Quiere estudiar",
            },
        ],
    },
    "datos_requeridos": ["dni", "nombres", "apellidos", "correo", "telefono"],
    "modalidad_pago": "previo",
}

# The four of version 1, by title, get a key; their texts do not change.
KEYS_OF_V1 = {
    "Si piden factura": "educacion.factura",
    "Si preguntan por convenios o descuentos institucionales": "educacion.convenios",
    "Si quieren certificar a un grupo": "educacion.grupo",
    "Si preguntan por la malla curricular": "educacion.malla",
}

NEW_SITUATIONS = [
    {
        "clave": "educacion.nombre_primero",
        "titulo": "Saludar y pedir el nombre",
        "cuando_aplica": "Es el primer mensaje del cliente en la conversación.",
        "que_hace": (
            "Salúdalo con calidez, preséntate y pregúntale su nombre. Si en ese mismo mensaje ya "
            "preguntó algo, respóndelo también: nunca condiciones la respuesta a su nombre."
        ),
        "campos": {},
        # About the moment, not the customer's words: it always enters, only on the first message.
        "etapas": ["INICIO"],
        "siempre": True,
    },
    {
        "clave": "educacion.precio_por_certificado",
        "titulo": "Precio por certificado antes del paquete",
        "cuando_aplica": (
            "El cliente pregunta por los paquetes o por el precio de varias certificaciones y aún "
            "no eligió."
        ),
        "que_hace": (
            "Dile primero el precio de una certificación individual y después los paquetes, para "
            "que vea cuánto ahorra. Copia los precios tal cual."
        ),
        "campos": {},
    },
    {
        "clave": "educacion.cualquier_tema",
        "titulo": "Se certifica cualquier tema",
        "cuando_aplica": "El cliente quiere certificar un tema que no aparece en el catálogo.",
        "que_hace": (
            "Confírmale que ese tema se puede certificar, con el nombre que él escribió. No digas "
            "que no lo tienen ni lo pases a una persona por eso."
        ),
        "campos": {},
    },
    {
        "clave": "educacion.no_sabe_tema",
        "titulo": "No sabe qué tema certificar",
        "cuando_aplica": "El cliente quiere certificarse pero no sabe en qué tema.",
        "que_hace": (
            "Pregúntale en qué área trabaja o trabajó y muéstrale las categorías con "
            "mostrar_opciones."
        ),
        "campos": {},
    },
    {
        "clave": "educacion.programa_completo",
        "titulo": "Quiere estudiar un programa",
        "cuando_aplica": "El cliente quiere llevar un curso o programa completo, no solo certificar.",
        "que_hace": (
            "Envíale la malla si existe el recurso, pregúntale qué programa le interesa y en qué "
            "horario puede estudiar, y pásalo a una persona con ese resumen."
        ),
        "campos": {},
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    v1 = bind.execute(
        sa.text(
            "SELECT id FROM catalog_versions WHERE kind = 'rubro' AND key = :k AND version = 1"
        ),
        {"k": RUBRO},
    ).scalar_one_or_none()
    if v1 is None:
        return
    v2 = bind.execute(
        sa.text(
            "INSERT INTO catalog_versions (kind, key, version, estado, notas, plantilla) "
            "VALUES ('rubro', :k, 2, 'borrador', :n, CAST(:p AS jsonb)) RETURNING id"
        ),
        {"k": RUBRO, "n": NOTE, "p": json.dumps(PLANTILLA, ensure_ascii=False)},
    ).scalar_one()

    for table, columns in (
        ("catalog_rules", "rule_key, etapa, texto"),
        ("catalog_faqs", "pregunta, respuesta"),
        ("catalog_objections", "objecion, manejo"),
    ):
        bind.execute(
            sa.text(
                f"INSERT INTO {table} (version_id, orden, {columns}) "
                f"SELECT :v2, orden, {columns} FROM {table} WHERE version_id = :v1"
            ),
            {"v1": v1, "v2": v2},
        )

    old = bind.execute(
        sa.text(
            "SELECT orden, titulo, cuando_aplica, que_hace, campos "
            "FROM catalog_suggested_situations WHERE version_id = :v1 ORDER BY orden"
        ),
        {"v1": v1},
    ).all()
    rows = [
        {
            "clave": KEYS_OF_V1.get(row.titulo),
            "titulo": row.titulo,
            "cuando_aplica": row.cuando_aplica,
            "que_hace": row.que_hace,
            "campos": json.dumps(row.campos, ensure_ascii=False),
            "etapas": None,
            "siempre": False,
        }
        for row in old
    ] + [
        {
            "etapas": None,
            "siempre": False,
            **s,
            "campos": json.dumps(s["campos"]),
        }
        for s in NEW_SITUATIONS
    ]
    bind.execute(
        sa.text(
            "INSERT INTO catalog_suggested_situations "
            "(version_id, orden, clave, titulo, cuando_aplica, que_hace, campos, etapas, siempre) "
            "VALUES (:v, :orden, :clave, :titulo, :cuando_aplica, :que_hace, "
            "CAST(:campos AS jsonb), CAST(:etapas AS text[]), :siempre)"
        ),
        [{"v": v2, "orden": i, **row} for i, row in enumerate(rows, start=1)],
    )
    bind.execute(
        sa.text(
            "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() WHERE id = :v"
        ),
        {"v": v2},
    )


def downgrade() -> None:
    op.execute("ALTER TABLE catalog_rules DISABLE TRIGGER catalog_rules_frozen")
    op.execute("ALTER TABLE catalog_faqs DISABLE TRIGGER catalog_faqs_frozen")
    op.execute("ALTER TABLE catalog_objections DISABLE TRIGGER catalog_objections_frozen")
    op.execute(
        "ALTER TABLE catalog_suggested_situations DISABLE TRIGGER catalog_suggested_situations_frozen"
    )
    version = (
        "SELECT id FROM catalog_versions WHERE kind = 'rubro' AND key = 'instituto' AND version = 2"
    )
    op.execute(f"DELETE FROM catalog_embeddings WHERE version_id IN ({version})")
    for table in (
        "catalog_rules",
        "catalog_faqs",
        "catalog_objections",
        "catalog_suggested_situations",
    ):
        op.execute(f"DELETE FROM {table} WHERE version_id IN ({version})")
    op.execute(
        "UPDATE tenant_catalog_seen SET version_vista = 1 "
        "WHERE kind = 'rubro' AND key = 'instituto' AND version_vista = 2"
    )
    op.execute(
        "UPDATE agent_configs SET plantilla_rubro_version = 1 WHERE plantilla_rubro_version = 2 "
        "AND tenant_id IN (SELECT tenant_id FROM business_profiles WHERE industry = 'instituto')"
    )
    op.execute(
        "DELETE FROM catalog_versions WHERE kind = 'rubro' AND key = 'instituto' AND version = 2"
    )
    op.execute("ALTER TABLE catalog_rules ENABLE TRIGGER catalog_rules_frozen")
    op.execute("ALTER TABLE catalog_faqs ENABLE TRIGGER catalog_faqs_frozen")
    op.execute("ALTER TABLE catalog_objections ENABLE TRIGGER catalog_objections_frozen")
    op.execute(
        "ALTER TABLE catalog_suggested_situations ENABLE TRIGGER catalog_suggested_situations_frozen"
    )
