"""The universal sales process, version 1 (feature 020).

Revision ID: 0071
Revises: 0070

One process for every business that sells products, services or both, written from scratch (Giannier,
2026-10-28). Its rules come from `alembic/data/proceso_universal_v1.json`, generated once from
`app/process/templates/universal.py` and frozen from then on, like `procesos_v1.json`. The versions of
`productos`, `servicios` and `certificaciones` are not touched: those templates stay as they are until
no business uses them.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: str | Sequence[str] | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA = Path(__file__).resolve().parents[1] / "data" / "proceso_universal_v1.json"
NOTE = "v1: proceso universal, escrito desde cero (feature 020)"


def upgrade() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    bind = op.get_bind()
    version_id = bind.execute(
        sa.text(
            "INSERT INTO catalog_versions (kind, key, version, estado, notas) "
            "VALUES ('proceso', :key, 1, 'borrador', :notas) RETURNING id"
        ),
        {"key": data["key"], "notas": NOTE},
    ).scalar_one()
    bind.execute(
        sa.text(
            "INSERT INTO catalog_rules (version_id, orden, rule_key, etapa, texto) "
            "VALUES (:version_id, :orden, :rule_key, :etapa, :texto)"
        ),
        [
            {"version_id": version_id, "orden": i, **rule}
            for i, rule in enumerate(data["reglas"], start=1)
        ],
    )
    bind.execute(
        sa.text(
            "UPDATE catalog_versions SET estado = 'publicada', publicada_en = now() WHERE id = :v"
        ),
        {"v": version_id},
    )


def downgrade() -> None:
    # The rules of a published version are frozen by trigger; lifted for this one step.
    op.execute("ALTER TABLE catalog_rules DISABLE TRIGGER catalog_rules_frozen")
    op.execute(
        "DELETE FROM catalog_rules WHERE version_id IN (SELECT id FROM catalog_versions "
        "WHERE kind = 'proceso' AND key = 'universal')"
    )
    op.execute(
        "DELETE FROM tenant_rule_switches WHERE kind = 'proceso' AND rule_key LIKE 'universal.%'"
    )
    op.execute("DELETE FROM tenant_catalog_seen WHERE kind = 'proceso' AND key = 'universal'")
    op.execute("DELETE FROM catalog_versions WHERE kind = 'proceso' AND key = 'universal'")
    op.execute("ALTER TABLE catalog_rules ENABLE TRIGGER catalog_rules_frozen")
