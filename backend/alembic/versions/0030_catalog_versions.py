"""Versioned catalogue of process templates and rubros (Feature 017, TASK-460)

* `rubros`, `catalog_versions` and their children (`catalog_rules`,
  `catalog_faqs`, `catalog_objections`, `catalog_suggested_situations`):
  platform data, no `tenant_id` (017 plan, "Catálogo de plataforma").
* A trigger keeps a `publicada` version frozen: its children cannot be added,
  changed or removed, and the version row itself may only move to `retirada`.
  A change is a new version (017, C-04).
* Data: the three process templates as **proceso v1 publicada**, cut into rules
  from the code texts (`alembic/data/procesos_v1.json`, frozen by
  `scripts/build_procesos_v1.py`), and the six rubros v1
  (`alembic/data/rubros_v1.json`), written for Giannier to review.
* `process_stage_overrides` is dropped: stages are no longer edited, their rules
  are switched off (017, C-01). No real data used it.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-14
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA = Path(__file__).resolve().parents[1] / "data"
CHILDREN = (
    "catalog_rules",
    "catalog_faqs",
    "catalog_objections",
    "catalog_suggested_situations",
)


def _child_table(name: str, *columns: sa.Column[Any]) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False),
        *columns,
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["catalog_versions.id"],
            name=op.f(f"fk_{name}_version_id_catalog_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
    )
    op.create_index(op.f(f"ix_{name}_version_id"), name, ["version_id"])


def _schema() -> None:
    op.create_table(
        "rubros",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("plantilla_sugerida", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_rubros")),
    )
    op.create_table(
        "catalog_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="borrador", nullable=False),
        sa.Column("evals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("publicada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publicada_por", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('proceso', 'rubro')", name=op.f("ck_catalog_versions_kind")),
        sa.CheckConstraint(
            "estado IN ('borrador', 'publicada', 'retirada')",
            name=op.f("ck_catalog_versions_estado"),
        ),
        sa.ForeignKeyConstraint(
            ["publicada_por"], ["users.id"], name=op.f("fk_catalog_versions_publicada_por_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_versions")),
        sa.UniqueConstraint(
            "kind", "key", "version", name=op.f("uq_catalog_versions_kind_key_version")
        ),
    )
    _child_table(
        "catalog_rules",
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("etapa", sa.Text(), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
    )
    op.create_unique_constraint(
        op.f("uq_catalog_rules_version_id_rule_key"), "catalog_rules", ["version_id", "rule_key"]
    )
    _child_table(
        "catalog_faqs",
        sa.Column("pregunta", sa.Text(), nullable=False),
        sa.Column("respuesta", sa.Text(), nullable=False),
    )
    _child_table(
        "catalog_objections",
        sa.Column("objecion", sa.Text(), nullable=False),
        sa.Column("manejo", sa.Text(), nullable=False),
    )
    _child_table(
        "catalog_suggested_situations",
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("cuando_aplica", sa.Text(), nullable=False),
        sa.Column("que_hace", sa.Text(), nullable=False),
        sa.Column(
            "campos",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def _freeze_published() -> None:
    op.execute(
        """
        CREATE FUNCTION catalog_version_frozen() RETURNS trigger AS $$
        BEGIN
            IF OLD.estado <> 'borrador' AND (
                NEW.estado = 'borrador'
                OR (OLD.estado = 'retirada' AND NEW.estado <> 'retirada')
                OR NEW.kind IS DISTINCT FROM OLD.kind
                OR NEW.key IS DISTINCT FROM OLD.key
                OR NEW.version IS DISTINCT FROM OLD.version
            ) THEN
                RAISE EXCEPTION 'catalog version % v% is %: make a new version',
                    OLD.key, OLD.version, OLD.estado;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER catalog_versions_frozen BEFORE UPDATE ON catalog_versions
            FOR EACH ROW EXECUTE FUNCTION catalog_version_frozen();

        CREATE FUNCTION catalog_child_frozen() RETURNS trigger AS $$
        DECLARE
            state text;
        BEGIN
            SELECT estado INTO state FROM catalog_versions
                WHERE id = COALESCE(NEW.version_id, OLD.version_id);
            IF state IS NOT NULL AND state <> 'borrador' THEN
                RAISE EXCEPTION 'catalog version is %: make a new version', state;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in CHILDREN:
        op.execute(
            f"CREATE TRIGGER {table}_frozen BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION catalog_child_frozen()"
        )


def _version(kind: str, key: str) -> Any:
    return (
        op.get_bind()
        .execute(
            sa.text(
                "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
                "VALUES (:kind, :key, 1, 'borrador', 'v1 inicial (TASK-460)') RETURNING id"
            ),
            {"kind": kind, "key": key},
        )
        .scalar_one()
    )


def _insert(table: str, version_id: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    names = ", ".join(["version_id", "orden", *columns])
    values = ", ".join([":version_id", ":orden", *(f":{c}" for c in columns)])
    if table == "catalog_suggested_situations":
        values = values.replace(":campos", "CAST(:campos AS jsonb)")
    op.get_bind().execute(
        sa.text(f"INSERT INTO {table} ({names}) VALUES ({values})"),
        [{"version_id": version_id, "orden": i, **row} for i, row in enumerate(rows, start=1)],
    )


def _seed() -> None:
    procesos = json.loads((DATA / "procesos_v1.json").read_text(encoding="utf-8"))["procesos"]
    for proceso in procesos:
        version_id = _version("proceso", proceso["key"])
        _insert("catalog_rules", version_id, proceso["reglas"])

    rubros = json.loads((DATA / "rubros_v1.json").read_text(encoding="utf-8"))["rubros"]
    for rubro in rubros:
        op.get_bind().execute(
            sa.text("INSERT INTO rubros (key, nombre, plantilla_sugerida) VALUES (:k, :n, :p)"),
            {"k": rubro["key"], "n": rubro["nombre"], "p": rubro["plantilla_sugerida"]},
        )
        version_id = _version("rubro", rubro["key"])
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
    op.execute(
        "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() "
        "WHERE estado = 'borrador'"
    )


def upgrade() -> None:
    _schema()
    _freeze_published()
    _seed()
    op.drop_table("process_stage_overrides")


def downgrade() -> None:
    op.create_table(
        "process_stage_overrides",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("stage_key", sa.Text(), nullable=False),
        sa.Column("instrucciones", sa.Text(), nullable=True),
        sa.Column("instrucciones_seguimiento", sa.Text(), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(instrucciones) <= 4000",
            name=op.f("ck_process_stage_overrides_instrucciones_length"),
        ),
        sa.CheckConstraint(
            "char_length(instrucciones_seguimiento) <= 1000",
            name=op.f("ck_process_stage_overrides_instrucciones_seguimiento_length"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_process_stage_overrides_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_process_stage_overrides_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "template_key", "stage_key", name=op.f("pk_process_stage_overrides")
        ),
    )
    for table in reversed(CHILDREN):
        op.drop_table(table)
    op.drop_table("catalog_versions")
    op.drop_table("rubros")
    op.execute("DROP FUNCTION catalog_child_frozen()")
    op.execute("DROP FUNCTION catalog_version_frozen()")
