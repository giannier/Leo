"""tenant_notifications: one-shot notices that have already been sent

Backs the trial notifier and the wizard reminder (TASK-019). Both are jobs on
a timer, so both need to know what has already gone out: a job that re-reads
`trial_ends_at` and re-decides would mail the customer again on every run,
after every worker restart, and twice if two workers pick up the same tick.

The composite primary key is the guarantee, not a `SELECT` in front of the
send. An `INSERT` either succeeds or violates the key, and that violation is
the answer to "was this already sent?".

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_notifications",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('trial_ending_3d', 'trial_ending_1d', 'trial_ended', 'wizard_reminder')",
            name=op.f("ck_tenant_notifications_tenant_notifications_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_notifications_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "kind", name=op.f("pk_tenant_notifications")),
    )


def downgrade() -> None:
    op.drop_table("tenant_notifications")
