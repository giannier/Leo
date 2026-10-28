"""Booking rules of seven rubros that work with the agenda (feature 020, T2.7).

Revision ID: 0074
Revises: 0073

Found by the simulated conversations of feature 020: the rubro `belleza` said «para reservar toma el
servicio, el día y la hora preferidos y deriva para que una persona confirme; nunca confirmes un
horario por tu cuenta», while the AGENDA block of a business with an agenda (feature 019) says «llama
a agendar_cita y NO derives», and the prompt ranks the RUBRO above the PROCESS. A model that obeys
hands every booking to a person. Seven rubros carried a rule like that.

A new version of each, with that rule conditioned on having an agenda; the same `rule_key`, so a
business that switched it off keeps it off, and one that wrote it in its own words keeps its words
(it sees the change as an improved rule). Everything else is copied as published.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | Sequence[str] | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTE = "reglas de reserva con agenda (feature 020, T2.7)"

RULES = {
    "belleza": (
        "rubro.reservas_con_persona",
        "Si tienes agenda (bloque AGENDA), reserva con ella. Si no, toma el servicio, el día y la "
        "hora preferidos y deriva para que una persona confirme la disponibilidad; nunca confirmes "
        "un horario por tu cuenta.",
    ),
    "salud": (
        "rubro.citas",
        "Si tienes agenda (bloque AGENDA), agenda la cita con ella. Si no, toma especialidad, día y "
        "hora preferidos y deriva para confirmar; no confirmes horarios por tu cuenta.",
    ),
    "servicios": (
        "rubro.agenda",
        "Si pide una cita o visita y tienes agenda (bloque AGENDA), resérvala con ella. Si no, toma "
        "día y hora preferidos y deriva para confirmar; no confirmes horarios por tu cuenta.",
    ),
    "hogar": (
        "rubro.visita_con_persona",
        "Para una visita técnica: si tienes agenda (bloque AGENDA), resérvala con ella; si no, la "
        "confirma una persona: toma día y hora preferidos y deriva.",
    ),
    "inmobiliaria": (
        "rubro.visita_con_persona",
        "Para una visita: si tienes agenda (bloque AGENDA), resérvala con ella; si no, toma día y "
        "hora preferidos y deriva para que un asesor la confirme.",
    ),
    "automotriz": (
        "rubro.prueba_de_manejo_con_persona",
        "Para una prueba de manejo: si tienes agenda (bloque AGENDA), resérvala con ella; si no, "
        "toma modelo, día y hora preferidos y deriva para confirmar.",
    ),
    "turismo": (
        "rubro.sin_confirmar_reservas",
        "Si tienes agenda (bloque AGENDA), reserva con ella. Si no, no confirmes disponibilidad ni "
        "reservas por tu cuenta: los precios y lo que incluye salen del catálogo y la reserva la "
        "confirma una persona.",
    ),
}

_CHILDREN = (
    ("catalog_faqs", "pregunta, respuesta"),
    ("catalog_objections", "objecion, manejo"),
    (
        "catalog_suggested_situations",
        "clave, titulo, cuando_aplica, que_hace, campos, etapas, siempre",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    for key, (rule_key, text) in RULES.items():
        source = bind.execute(
            sa.text(
                "SELECT id, version, plantilla FROM catalog_versions "
                "WHERE kind = 'rubro' AND key = :k AND estado = 'publicada' "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"k": key},
        ).one_or_none()
        if source is None:
            continue
        has_rule = bind.execute(
            sa.text("SELECT 1 FROM catalog_rules WHERE version_id = :v AND rule_key = :r"),
            {"v": source.id, "r": rule_key},
        ).first()
        if has_rule is None:
            continue
        latest = bind.execute(
            sa.text("SELECT max(version) FROM catalog_versions WHERE kind = 'rubro' AND key = :k"),
            {"k": key},
        ).scalar_one()
        new = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas, plantilla) "
                "SELECT 'rubro', :k, :n, 'borrador', :note, plantilla FROM catalog_versions "
                "WHERE id = :src RETURNING id"
            ),
            {"k": key, "n": latest + 1, "note": NOTE, "src": source.id},
        ).scalar_one()
        bind.execute(
            sa.text(
                "INSERT INTO catalog_rules (version_id, orden, rule_key, etapa, texto) "
                "SELECT :new, orden, rule_key, etapa, "
                "CASE WHEN rule_key = :r THEN :t ELSE texto END "
                "FROM catalog_rules WHERE version_id = :src"
            ),
            {"new": new, "src": source.id, "r": rule_key, "t": text},
        )
        for table, columns in _CHILDREN:
            bind.execute(
                sa.text(
                    f"INSERT INTO {table} (version_id, orden, {columns}) "
                    f"SELECT :new, orden, {columns} FROM {table} WHERE version_id = :src"
                ),
                {"new": new, "src": source.id},
            )
        bind.execute(
            sa.text(
                "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": new},
        )


def downgrade() -> None:
    tables = ("catalog_rules", "catalog_faqs", "catalog_objections", "catalog_suggested_situations")
    for table in tables:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {table}_frozen")
    ours = f"SELECT id FROM catalog_versions WHERE kind = 'rubro' AND notas = '{NOTE}'"
    # A business that saw or applied one of these goes back to the version before it.
    op.execute(
        "UPDATE tenant_catalog_seen s SET version_vista = s.version_vista - 1 "
        "FROM catalog_versions v WHERE v.kind = 'rubro' AND v.notas = "
        f"'{NOTE}' AND s.kind = 'rubro' AND s.key = v.key AND s.version_vista = v.version"
    )
    op.execute(f"DELETE FROM catalog_embeddings WHERE version_id IN ({ours})")
    for table in tables:
        op.execute(f"DELETE FROM {table} WHERE version_id IN ({ours})")
    op.execute(f"DELETE FROM catalog_versions WHERE id IN ({ours})")
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {table}_frozen")
