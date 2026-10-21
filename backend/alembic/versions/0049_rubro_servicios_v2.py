"""Rubro "servicios" v2: a quote can carry a reference price.

Revision ID: 0049
Revises: 0048

v1 said "a quote service has no price in the chat". A real services business sells some things at a fixed
price (hosting), some by quote with a reference price ("a web page from S/ 800") and some by pure quote
(custom systems). v2 keeps everything of v1 and changes the two rules that contradicted that, so the
assistant says a reference price as "desde" and keeps firm prices firm. Businesses see it as news.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | Sequence[str] | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTE = "v2: el precio referencial de una cotización se dice como «desde», y el precio fijo se dice sin rodeos"
CHANGED = {
    "Un servicio de cotización no tiene precio en el chat: junta los datos clave y deriva.": (
        "Un servicio de cotización no tiene precio final en el chat: si tiene precio referencial, "
        "dilo como «desde» y aclara que se cotiza; junta los datos clave y deriva con un resumen."
    ),
    "Entiende la necesidad antes de dar precios: los servicios se adaptan al caso.": (
        "Entiende la necesidad antes de recomendar: los servicios se adaptan al caso. El precio fijo "
        "se dice sin rodeos y el de cotización se presenta como referencial."
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    v1 = bind.execute(
        sa.text(
            "SELECT id FROM catalog_versions WHERE kind = 'rubro' AND key = 'servicios' "
            "AND version = 1"
        )
    ).scalar_one_or_none()
    if v1 is None:
        return
    v2 = bind.execute(
        sa.text(
            "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
            "VALUES ('rubro', 'servicios', 2, 'borrador', :notas) RETURNING id"
        ),
        {"notas": NOTE},
    ).scalar_one()
    for rule in bind.execute(
        sa.text("SELECT rule_key, etapa, texto, orden FROM catalog_rules WHERE version_id = :v"),
        {"v": v1},
    ).all():
        bind.execute(
            sa.text(
                "INSERT INTO catalog_rules (version_id, orden, rule_key, etapa, texto) "
                "VALUES (:v, :orden, :rule_key, :etapa, :texto)"
            ),
            {
                "v": v2,
                "orden": rule.orden,
                "rule_key": rule.rule_key,
                "etapa": rule.etapa,
                "texto": CHANGED.get(rule.texto, rule.texto),
            },
        )
    bind.execute(
        sa.text(
            "INSERT INTO catalog_faqs (version_id, orden, pregunta, respuesta) "
            "SELECT :n, orden, pregunta, respuesta FROM catalog_faqs WHERE version_id = :o"
        ),
        {"n": v2, "o": v1},
    )
    bind.execute(
        sa.text(
            "INSERT INTO catalog_objections (version_id, orden, objecion, manejo) "
            "SELECT :n, orden, objecion, manejo FROM catalog_objections WHERE version_id = :o"
        ),
        {"n": v2, "o": v1},
    )
    bind.execute(
        sa.text(
            "INSERT INTO catalog_suggested_situations "
            "(version_id, orden, titulo, cuando_aplica, que_hace, campos) "
            "SELECT :n, orden, titulo, cuando_aplica, que_hace, campos "
            "FROM catalog_suggested_situations WHERE version_id = :o"
        ),
        {"n": v2, "o": v1},
    )
    bind.execute(
        sa.text(
            "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() WHERE id = :v"
        ),
        {"v": v2},
    )


def downgrade() -> None:
    # The rows of a published version are frozen by triggers; lifted for this one step.
    tables = ("catalog_rules", "catalog_faqs", "catalog_objections", "catalog_suggested_situations")
    for table in tables:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {table}_frozen")
    for table in tables:
        op.execute(
            f"DELETE FROM {table} WHERE version_id IN (SELECT id FROM catalog_versions "
            "WHERE kind = 'rubro' AND key = 'servicios' AND version = 2)"
        )
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {table}_frozen")
    op.execute(
        "UPDATE tenant_catalog_seen SET version_vista = 1 "
        "WHERE kind = 'rubro' AND key = 'servicios' AND version_vista = 2"
    )
    op.execute("ALTER TABLE catalog_versions DISABLE TRIGGER catalog_versions_frozen")
    op.execute(
        "DELETE FROM catalog_versions WHERE kind = 'rubro' AND key = 'servicios' AND version = 2"
    )
    op.execute("ALTER TABLE catalog_versions ENABLE TRIGGER catalog_versions_frozen")
