"""Seven more rubros (feature 019).

Revision ID: 0052
Revises: 0051

The first six rubros (restaurante, instituto, retail, servicios, salud, otros) leave out most of what a
general-purpose agent platform is meant to serve. This adds technology, beauty and wellness, real estate,
automotive, home services, tourism and wholesale (B2B), each with its rules, five FAQs, five objections and
suggested situations in the same directive style as `rubros_v1.json` (they say HOW to answer with the
business's own data, never the data itself), and the capabilities that usually fit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | Sequence[str] | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA = Path(__file__).resolve().parent.parent / "data"
CONSTRAINT = "ck_business_profiles_business_profiles_industry"
BEFORE = ("restaurante", "instituto", "retail", "servicios", "salud", "otros")
NEW = ("tecnologia", "belleza", "inmobiliaria", "automotriz", "hogar", "turismo", "b2b")
CHILDREN = ("catalog_rules", "catalog_faqs", "catalog_objections", "catalog_suggested_situations")


def _in(values: Sequence[str]) -> str:
    return "industry IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _insert(table: str, version_id: Any, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0])
    names = ", ".join(["version_id", "orden", *columns])
    values = ", ".join([":version_id", ":orden", *(f":{c}" for c in columns)])
    if table == "catalog_suggested_situations":
        values = values.replace(":campos", "CAST(:campos AS jsonb)")
    op.get_bind().execute(
        sa.text(f"INSERT INTO {table} ({names}) VALUES ({values})"),
        [{"version_id": version_id, "orden": i, **row} for i, row in enumerate(rows, start=1)],
    )


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "business_profiles", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "business_profiles", _in((*BEFORE[:5], *NEW, "otros"))
    )

    bind = op.get_bind()
    rubros = json.loads((DATA / "rubros_v2.json").read_text(encoding="utf-8"))["rubros"]
    assert tuple(r["key"] for r in rubros) == NEW
    for rubro in rubros:
        bind.execute(
            sa.text(
                "INSERT INTO rubros (key, nombre, plantilla_sugerida, capacidades_sugeridas) "
                "VALUES (:k, :n, :p, :c)"
            ),
            {
                "k": rubro["key"],
                "n": rubro["nombre"],
                "p": rubro["plantilla_sugerida"],
                "c": rubro["capacidades_sugeridas"],
            },
        )
        version_id = bind.execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES ('rubro', :key, 1, 'borrador', 'v1 inicial (019)') RETURNING id"
            ),
            {"key": rubro["key"]},
        ).scalar_one()
        _insert(
            "catalog_rules",
            version_id,
            [
                {"rule_key": f"rubro.{key}", "etapa": None, "texto": text}
                for key, text in rubro["reglas"]
            ],
        )
        _insert(
            "catalog_faqs", version_id, [{"pregunta": q, "respuesta": a} for q, a in rubro["faqs"]]
        )
        _insert(
            "catalog_objections",
            version_id,
            [{"objecion": o, "manejo": m} for o, m in rubro["objeciones"]],
        )
        _insert(
            "catalog_suggested_situations",
            version_id,
            [
                {"titulo": t, "cuando_aplica": c, "que_hace": q, "campos": json.dumps(f)}
                for t, c, q, f in rubro["situaciones"]
            ],
        )
        bind.execute(
            sa.text(
                "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() "
                "WHERE id = :v"
            ),
            {"v": version_id},
        )


def downgrade() -> None:
    keys = ", ".join(f"'{k}'" for k in NEW)
    op.execute(f"UPDATE business_profiles SET industry = 'otros' WHERE industry IN ({keys})")
    op.drop_constraint(op.f(CONSTRAINT), "business_profiles", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "business_profiles", _in(BEFORE))

    # The rows of a published version are frozen by triggers; lifted for this one step.
    for table in CHILDREN:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {table}_frozen")
    for table in CHILDREN:
        op.execute(
            f"DELETE FROM {table} WHERE version_id IN (SELECT id FROM catalog_versions "
            f"WHERE kind = 'rubro' AND key IN ({keys}))"
        )
    for table in CHILDREN:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {table}_frozen")
    op.execute(f"DELETE FROM tenant_catalog_seen WHERE kind = 'rubro' AND key IN ({keys})")
    op.execute("ALTER TABLE catalog_versions DISABLE TRIGGER catalog_versions_frozen")
    op.execute(f"DELETE FROM catalog_versions WHERE kind = 'rubro' AND key IN ({keys})")
    op.execute("ALTER TABLE catalog_versions ENABLE TRIGGER catalog_versions_frozen")
    op.execute(f"DELETE FROM rubros WHERE key IN ({keys})")
