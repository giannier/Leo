"""Every paid plan may use every intelligence level; the trial only the economic one

Feature 004 (Motor de ventas), Milestone B, decision 4: levels are not locked by
plan. All paid plans may choose `economico`, `medio` and `mejor`, and each level
spends credits according to its real cost (metering is TASK-445 and feature 009).
The free trial keeps `economico` only.

Data only: `plans.allowed_intelligence` (0016) already holds the list.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIAL_PLAN = "trial"


def upgrade() -> None:
    op.execute(
        "UPDATE plans SET allowed_intelligence = ARRAY['economico', 'medio', 'mejor']::text[] "
        f"WHERE id <> '{TRIAL_PLAN}'"
    )
    op.execute(
        "UPDATE plans SET allowed_intelligence = ARRAY['economico']::text[] "
        f"WHERE id = '{TRIAL_PLAN}'"
    )


def downgrade() -> None:
    op.execute("UPDATE plans SET allowed_intelligence = ARRAY['economico']::text[]")
