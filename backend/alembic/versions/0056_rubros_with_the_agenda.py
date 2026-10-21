"""The rubros that book know the agenda exists (feature 019, phase D).

Revision ID: 0056
Revises: 0055

Seven rubros said "take the day and the time and hand over; never confirm a time yourself". True until
the agent can book: with the `agendar` capability it has `consultar_horarios`, `agendar_cita` and
`gestionar_cita`, and those instructions would contradict its own tools. Each of them now says both
things (use the tools if you have them; otherwise take the data and hand over), so one version serves a
business with and without the agenda. The rest of every version is copied as it was; businesses see the
change as news. The rubros that usually book also suggest the `agendar` capability.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | Sequence[str] | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTE = "con agenda: si tienes las herramientas de agenda, úsalas; si no, toma los datos y deriva"
TOOLS = "con consultar_horarios ofrece hasta 3 horarios y agenda con agendar_cita"

# rubro -> {rule_key: text}
RULES: dict[str, dict[str, str]] = {
    "servicios": {
        "agenda": (
            f"Si pide una cita, visita o reunión: {TOOLS}; sin esas herramientas, toma día y hora "
            "preferidos y deriva para confirmar. Nunca inventes un horario."
        )
    },
    "salud": {
        "citas": (
            f"Para citas: {TOOLS}; sin esas herramientas, toma especialidad, día y hora preferidos y "
            "deriva. Nunca inventes un horario."
        )
    },
    "belleza": {
        "reservas_con_persona": (
            f"Para reservar: {TOOLS}; sin esas herramientas, toma servicio, día y hora preferidos y "
            "deriva para que una persona confirme. Nunca inventes un horario."
        )
    },
    "inmobiliaria": {
        "visita_con_persona": (
            f"Para una visita: {TOOLS}; sin esas herramientas, toma día y hora preferidos y deriva a "
            "un asesor."
        )
    },
    "automotriz": {
        "prueba_de_manejo_con_persona": (
            f"Para una prueba de manejo: {TOOLS}; sin esas herramientas, toma modelo, día y hora "
            "preferidos y deriva para confirmar."
        )
    },
    "hogar": {
        "visita_con_persona": (
            f"La visita técnica: {TOOLS}; sin esas herramientas, toma día y hora preferidos y deriva."
        )
    },
}

# rubro -> {pregunta: respuesta}
FAQS: dict[str, dict[str, str]] = {
    "belleza": {
        "¿Tienen disponibilidad esta semana?": (
            "Con consultar_horarios ofrece lo que devuelva. Sin esa herramienta no confirmes horarios: "
            "toma servicio, día y hora preferidos y deriva a una persona."
        )
    },
    "inmobiliaria": {
        "¿Se puede visitar?": (
            "Con consultar_horarios ofrece horarios de visita y agenda con agendar_cita; sin esa "
            "herramienta, toma día y hora preferidos y su nombre y deriva a un asesor."
        )
    },
    "automotriz": {
        "¿Puedo probarlo?": (
            "Con consultar_horarios ofrece horarios y agenda la prueba con agendar_cita; sin esa "
            "herramienta, toma modelo, día y hora preferidos y deriva a un asesor."
        )
    },
    "hogar": {
        "¿Hacen visita técnica?": (
            "Responde con la política de visitas del negocio (costo, descuento si contrata, zonas) y "
            "ofrece horarios con consultar_horarios; sin esa herramienta, toma día y hora preferidos "
            "para derivar."
        )
    },
}

# rubro -> {titulo: que_hace}
SITUATIONS: dict[str, dict[str, str]] = {
    "servicios": {
        "Si piden una visita técnica": (
            "Pide dirección y ofrece horarios con consultar_horarios para agendar con agendar_cita; "
            "sin esas herramientas, toma día y hora preferidos y deriva."
        )
    },
    "salud": {
        "Si quieren reprogramar una cita": (
            "Con gestionar_cita cambia la cita; sin esa herramienta, toma el nuevo día y hora "
            "preferidos y deriva para confirmar."
        )
    },
    "belleza": {
        "Si quiere reservar": (
            "Con consultar_horarios ofrece hasta 3 horarios y agenda con agendar_cita cuando tengas su "
            "nombre; sin esas herramientas, toma servicio, día, hora y nombre y deriva con motivo «asesoria»."
        ),
        "Si pide cambiar o cancelar": (
            "Con gestionar_cita cambia o cancela su cita e informa la política de cambios del negocio; "
            "sin esa herramienta, toma su nombre y la cita actual y deriva a una persona."
        ),
    },
    "automotriz": {
        "Si pide una prueba de manejo": (
            "Con consultar_horarios ofrece horarios y agenda con agendar_cita; sin esas herramientas, "
            "toma modelo, día, hora, nombre y teléfono y deriva con motivo «asesoria»."
        )
    },
    "hogar": {
        "Si pide una visita técnica": (
            "Toma dirección y teléfono; con consultar_horarios ofrece horarios y agenda con "
            "agendar_cita; sin esas herramientas, toma día y hora preferidos y deriva con motivo «asesoria»."
        )
    },
    "tecnologia": {
        "Si piden una demo o reunión": (
            "Con consultar_horarios ofrece hasta 3 horarios y agenda la reunión con agendar_cita; sin "
            "esas herramientas, toma nombre, correo y horario preferido y deriva con motivo «asesoria»."
        )
    },
}

SUGGESTED = {
    "servicios": ["atender", "vender", "cotizar", "agendar"],
    "salud": ["atender", "agendar"],
    "belleza": ["atender", "vender", "agendar"],
    "inmobiliaria": ["atender", "cotizar", "agendar"],
    "automotriz": ["atender", "cotizar", "agendar"],
    "hogar": ["atender", "cotizar", "agendar"],
    "tecnologia": ["atender", "vender", "cotizar", "agendar"],
}
BEFORE = {
    "servicios": ["atender", "vender", "cotizar"],
    "salud": ["atender", "cotizar"],
    "belleza": ["atender", "vender"],
    "inmobiliaria": ["atender", "cotizar"],
    "automotriz": ["atender", "cotizar"],
    "hogar": ["atender", "cotizar"],
    "tecnologia": ["atender", "vender", "cotizar"],
}
RUBROS = tuple(SUGGESTED)
CHILDREN = ("catalog_rules", "catalog_faqs", "catalog_objections", "catalog_suggested_situations")


def _latest(bind: Any, key: str) -> tuple[Any, int]:
    row = bind.execute(
        sa.text(
            "SELECT id, version FROM catalog_versions WHERE kind = 'rubro' AND key = :k "
            "AND estado = 'publicada' ORDER BY version DESC LIMIT 1"
        ),
        {"k": key},
    ).one()
    return row.id, row.version


def upgrade() -> None:
    bind = op.get_bind()
    for key in RUBROS:
        old_id, version = _latest(bind, key)
        new_id = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES ('rubro', :k, :v, 'borrador', :n) RETURNING id"
            ),
            {"k": key, "v": version + 1, "n": NOTE},
        ).scalar_one()
        for rule in bind.execute(
            sa.text(
                "SELECT rule_key, etapa, texto, orden FROM catalog_rules WHERE version_id = :v"
            ),
            {"v": old_id},
        ).all():
            bind.execute(
                sa.text(
                    "INSERT INTO catalog_rules (version_id, orden, rule_key, etapa, texto) "
                    "VALUES (:v, :o, :k, :e, :t)"
                ),
                {
                    "v": new_id,
                    "o": rule.orden,
                    "k": rule.rule_key,
                    "e": rule.etapa,
                    "t": RULES.get(key, {}).get(rule.rule_key, rule.texto),
                },
            )
        for faq in bind.execute(
            sa.text("SELECT orden, pregunta, respuesta FROM catalog_faqs WHERE version_id = :v"),
            {"v": old_id},
        ).all():
            bind.execute(
                sa.text(
                    "INSERT INTO catalog_faqs (version_id, orden, pregunta, respuesta) "
                    "VALUES (:v, :o, :q, :a)"
                ),
                {
                    "v": new_id,
                    "o": faq.orden,
                    "q": faq.pregunta,
                    "a": FAQS.get(key, {}).get(faq.pregunta, faq.respuesta),
                },
            )
        bind.execute(
            sa.text(
                "INSERT INTO catalog_objections (version_id, orden, objecion, manejo) "
                "SELECT :n, orden, objecion, manejo FROM catalog_objections WHERE version_id = :o"
            ),
            {"n": new_id, "o": old_id},
        )
        for sit in bind.execute(
            sa.text(
                "SELECT orden, titulo, cuando_aplica, que_hace, campos "
                "FROM catalog_suggested_situations WHERE version_id = :v"
            ),
            {"v": old_id},
        ).all():
            bind.execute(
                sa.text(
                    "INSERT INTO catalog_suggested_situations "
                    "(version_id, orden, titulo, cuando_aplica, que_hace, campos) "
                    "VALUES (:v, :o, :t, :c, :q, CAST(:f AS jsonb))"
                ),
                {
                    "v": new_id,
                    "o": sit.orden,
                    "t": sit.titulo,
                    "c": sit.cuando_aplica,
                    "q": SITUATIONS.get(key, {}).get(sit.titulo, sit.que_hace),
                    "f": json.dumps(sit.campos),
                },
            )
        bind.execute(
            sa.text(
                "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() WHERE id = :v"
            ),
            {"v": new_id},
        )
        bind.execute(
            sa.text("UPDATE rubros SET capacidades_sugeridas = :c WHERE key = :k"),
            {"c": SUGGESTED[key], "k": key},
        )


def downgrade() -> None:
    bind = op.get_bind()
    keys = ", ".join(f"'{k}'" for k in RUBROS)
    for table in CHILDREN:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {table}_frozen")
    for table in CHILDREN:
        op.execute(
            f"DELETE FROM {table} WHERE version_id IN (SELECT id FROM catalog_versions "
            f"WHERE kind = 'rubro' AND key IN ({keys}) AND notas = '{NOTE}')"
        )
    for table in CHILDREN:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {table}_frozen")
    op.execute("ALTER TABLE catalog_versions DISABLE TRIGGER catalog_versions_frozen")
    op.execute(
        f"DELETE FROM catalog_versions WHERE kind = 'rubro' AND key IN ({keys}) "
        f"AND notas = '{NOTE}'"
    )
    op.execute("ALTER TABLE catalog_versions ENABLE TRIGGER catalog_versions_frozen")
    for key, caps in BEFORE.items():
        bind.execute(
            sa.text("UPDATE rubros SET capacidades_sugeridas = :c WHERE key = :k"),
            {"c": caps, "k": key},
        )
    # A business that had seen the newer version sees the older one as its latest again.
    op.execute(
        "DELETE FROM tenant_catalog_seen WHERE kind = 'rubro' AND key IN (" + keys + ") "
        "AND version_vista > (SELECT max(version) FROM catalog_versions v "
        "WHERE v.kind = 'rubro' AND v.key = tenant_catalog_seen.key)"
    )
