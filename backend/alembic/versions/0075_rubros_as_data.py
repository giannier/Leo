"""Rubros are data: a business's rubro points to the `rubros` table, and fifteen new ones (feature 020).

Revision ID: 0075
Revises: 0074

Until now the rubros a business could pick were written in four places (a CHECK constraint, the model,
the wizard's API and the panel). Giannier, 2026-10-29: his companies span some twenty lines of
business, and more will come; a SaaS adds a rubro as content, not as a code change. The constraint
becomes a foreign key to `rubros`, and the panel reads the list from the API.

The fifteen rubros of `data/rubros_v3.json` (water and soft drinks, cleaning products, agro,
accounting and tax, recruitment, audit and compliance, babies and children, insurance, debt
collection, parking, design and branding, medical supplies, occupational safety, law firms and
conciliation centres) arrive with their template: rules, FAQs, objections, keyed suggested
situations and the process options the template suggests (feature 020, F3).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: str | Sequence[str] | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA = Path(__file__).resolve().parent.parent / "data" / "rubros_v3.json"
CHECK = "ck_business_profiles_business_profiles_industry"
FOREIGN = "fk_business_profiles_industry_rubros"
BEFORE = (
    "restaurante",
    "instituto",
    "retail",
    "servicios",
    "salud",
    "tecnologia",
    "belleza",
    "inmobiliaria",
    "automotriz",
    "hogar",
    "turismo",
    "b2b",
    "otros",
)
CHILDREN = ("catalog_rules", "catalog_faqs", "catalog_objections", "catalog_suggested_situations")
NOTE = "v1 inicial (feature 020: rubros de las empresas de Giannier)"


def _rubros() -> list[dict[str, Any]]:
    return list(json.loads(DATA.read_text(encoding="utf-8"))["rubros"])


def _insert(table: str, version_id: Any, rows: list[dict[str, Any]], casts: dict[str, str]) -> None:
    columns = list(rows[0])
    names = ", ".join(["version_id", "orden", *columns])
    placeholders = (f"CAST(:{c} AS {casts[c]})" if c in casts else f":{c}" for c in columns)
    values = ", ".join([":version_id", ":orden", *placeholders])
    op.get_bind().execute(
        sa.text(f"INSERT INTO {table} ({names}) VALUES ({values})"),
        [{"version_id": version_id, "orden": i, **row} for i, row in enumerate(rows, start=1)],
    )


def upgrade() -> None:
    bind = op.get_bind()
    rubros = _rubros()
    for rubro in rubros:
        bind.execute(
            sa.text(
                "INSERT INTO rubros (key, nombre, plantilla_sugerida, capacidades_sugeridas, "
                "preguntas_calificacion, datos_a_pedir_sugeridos, aviso_politica) "
                "VALUES (:k, :n, :p, :c, :q, :d, :a)"
            ),
            {
                "k": rubro["key"],
                "n": rubro["nombre"],
                "p": rubro["plantilla_sugerida"],
                "c": rubro["capacidades_sugeridas"],
                "q": rubro["preguntas_calificacion"],
                "d": rubro["datos_a_pedir_sugeridos"],
                "a": rubro["aviso_politica"],
            },
        )
        version_id = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas, plantilla) "
                "VALUES ('rubro', :k, 1, 'borrador', :n, CAST(:p AS jsonb)) RETURNING id"
            ),
            {"k": rubro["key"], "n": NOTE, "p": json.dumps(rubro["plantilla"], ensure_ascii=False)},
        ).scalar_one()
        _insert(
            "catalog_rules",
            version_id,
            [
                {"rule_key": f"rubro.{key}", "etapa": None, "texto": text}
                for key, text in rubro["reglas"]
            ],
            {},
        )
        _insert(
            "catalog_faqs",
            version_id,
            [{"pregunta": q, "respuesta": a} for q, a in rubro["faqs"]],
            {},
        )
        _insert(
            "catalog_objections",
            version_id,
            [{"objecion": o, "manejo": m} for o, m in rubro["objeciones"]],
            {},
        )
        _insert(
            "catalog_suggested_situations",
            version_id,
            [
                {
                    "clave": s["clave"],
                    "titulo": s["titulo"],
                    "cuando_aplica": s["cuando_aplica"],
                    "que_hace": s["que_hace"],
                    "campos": json.dumps(s["campos"], ensure_ascii=False),
                    "etapas": s.get("etapas"),
                    "siempre": bool(s.get("siempre", False)),
                }
                for s in rubro["situaciones"]
            ],
            {"campos": "jsonb", "etapas": "text[]"},
        )
        bind.execute(
            sa.text(
                "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": version_id},
        )

    op.drop_constraint(op.f(CHECK), "business_profiles", type_="check")
    op.create_foreign_key(
        op.f(FOREIGN),
        "business_profiles",
        "rubros",
        ["industry"],
        ["key"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    keys = ", ".join(f"'{r['key']}'" for r in _rubros())
    op.drop_constraint(op.f(FOREIGN), "business_profiles", type_="foreignkey")
    op.execute(f"UPDATE business_profiles SET industry = 'otros' WHERE industry IN ({keys})")
    allowed = ", ".join(f"'{k}'" for k in BEFORE)
    op.create_check_constraint(op.f(CHECK), "business_profiles", f"industry IN ({allowed})")

    versions = f"SELECT id FROM catalog_versions WHERE kind = 'rubro' AND key IN ({keys})"
    for table in CHILDREN:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {table}_frozen")
    op.execute(f"DELETE FROM catalog_embeddings WHERE version_id IN ({versions})")
    for table in CHILDREN:
        op.execute(f"DELETE FROM {table} WHERE version_id IN ({versions})")
    for table in CHILDREN:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {table}_frozen")
    op.execute(f"DELETE FROM tenant_catalog_seen WHERE kind = 'rubro' AND key IN ({keys})")
    patterns = ", ".join(f"'{r['key']}.%'" for r in _rubros())
    op.execute(
        "UPDATE situations SET plantilla_clave = NULL, plantilla_version = NULL "
        f"WHERE plantilla_clave LIKE ANY (ARRAY[{patterns}])"
    )
    op.execute("ALTER TABLE catalog_versions DISABLE TRIGGER catalog_versions_frozen")
    op.execute(f"DELETE FROM catalog_versions WHERE kind = 'rubro' AND key IN ({keys})")
    op.execute("ALTER TABLE catalog_versions ENABLE TRIGGER catalog_versions_frozen")
    op.execute(f"DELETE FROM rubros WHERE key IN ({keys})")
