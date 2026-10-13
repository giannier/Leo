"""tenants.pais: the business's country (ISO 3166-1 alpha-2)

Feature 004, TASK-450 (Milestone B, decision 10): the phone prefix a customer
omits is the business's country's, Peru by default. Registration already sent a
country and the API dropped it ("no table has a country column"); it is stored on
the tenant because the tenant exists from the first request, before the wizard
writes the business profile.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("pais", sa.CHAR(2), server_default="PE", nullable=False))
    op.create_check_constraint(op.f("ck_tenants_pais_format"), "tenants", "pais ~ '^[A-Z]{2}$'")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tenants_pais_format"), "tenants", type_="check")
    op.drop_column("tenants", "pais")
