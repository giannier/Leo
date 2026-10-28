"""Rubro templates a business applies and owns (feature 020, phase F3).

Revision ID: 0072
Revises: 0071

The template of a rubro is a starting point (Giannier, 2026-10-28): the business applies it, and what
it brings becomes the business's own, to change, switch off or delete; an improvement the platform
publishes later arrives as news to accept or not, item by item (D3).

* `catalog_versions.plantilla` (JSONB): what a rubro version suggests beyond its rules, questions,
  objections and situations: the paths, the customer data to ask before charging and the payment.
  Frozen with the version, like everything else in it.
* `catalog_suggested_situations.clave`: the item's stable key across versions, so a business can be
  told what is new or improved. Rows written before it have none and are keyed by their title.
* `tenant_rule_switches.texto_propio`: the business's own wording of a rubro rule (D3: the rules are
  a starting point the business changes). Its rules stay rules, in the RUBRO block: as «siempre»
  situations they would take the situation selector's slots before the business's own.
* `situations.plantilla_clave` and `plantilla_version`: which item of which version of the template a
  situation came from. Never changed by the business's edits: they say where it started.
* `agent_configs.plantilla_rubro_version`: the version of the rubro template the business applied;
  NULL until it applies one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072"
down_revision: str | Sequence[str] | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The function of migration 0030, plus the template content: a published version does not change.
FROZEN_WITH_TEMPLATE = """
CREATE OR REPLACE FUNCTION catalog_version_frozen() RETURNS trigger AS $$
BEGIN
    IF OLD.estado <> 'borrador' AND (
        NEW.estado = 'borrador'
        OR (OLD.estado = 'retirada' AND NEW.estado <> 'retirada')
        OR NEW.kind IS DISTINCT FROM OLD.kind
        OR NEW.key IS DISTINCT FROM OLD.key
        OR NEW.version IS DISTINCT FROM OLD.version
        OR NEW.plantilla IS DISTINCT FROM OLD.plantilla
    ) THEN
        RAISE EXCEPTION 'catalog version % v% is %: make a new version',
            OLD.key, OLD.version, OLD.estado;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FROZEN_AS_IN_0030 = """
CREATE OR REPLACE FUNCTION catalog_version_frozen() RETURNS trigger AS $$
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
"""


def upgrade() -> None:
    op.add_column(
        "catalog_versions",
        sa.Column("plantilla", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(FROZEN_WITH_TEMPLATE)

    op.add_column("catalog_suggested_situations", sa.Column("clave", sa.Text(), nullable=True))
    # When a suggestion enters a turn, copied to the business's situation: «saludar y pedir el
    # nombre» is about the first message, not about words a customer says.
    op.add_column(
        "catalog_suggested_situations",
        sa.Column("etapas", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(
        "catalog_suggested_situations",
        sa.Column("siempre", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("tenant_rule_switches", sa.Column("texto_propio", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_tenant_rule_switches_texto_propio"),
        "tenant_rule_switches",
        "texto_propio IS NULL OR char_length(btrim(texto_propio)) BETWEEN 1 AND 600",
    )
    # A rule with its own text is not switched off: the switch row now carries either.
    op.alter_column("tenant_rule_switches", "apagada", server_default="false")

    op.add_column("situations", sa.Column("plantilla_clave", sa.Text(), nullable=True))
    op.add_column("situations", sa.Column("plantilla_version", sa.Integer(), nullable=True))

    op.add_column(
        "agent_configs", sa.Column("plantilla_rubro_version", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "plantilla_rubro_version")
    op.drop_column("situations", "plantilla_version")
    op.drop_column("situations", "plantilla_clave")
    op.alter_column("tenant_rule_switches", "apagada", server_default=None)
    op.drop_constraint(
        op.f("ck_tenant_rule_switches_texto_propio"), "tenant_rule_switches", type_="check"
    )
    op.drop_column("tenant_rule_switches", "texto_propio")
    op.drop_column("catalog_suggested_situations", "siempre")
    op.drop_column("catalog_suggested_situations", "etapas")
    op.drop_column("catalog_suggested_situations", "clave")
    op.execute(FROZEN_AS_IN_0030)
    op.drop_column("catalog_versions", "plantilla")
