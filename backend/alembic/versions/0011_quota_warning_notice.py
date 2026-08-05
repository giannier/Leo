"""tenant_notifications: add the 80%-of-messages warning

Feature 002, HU-04. The three existing notices are about the calendar; this
one is about the meter, and a trial can run out of messages a week before it
runs out of days. Without it the first sign a customer gets is the agent
going quiet.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_tenant_notifications_tenant_notifications_kind"
OLD_KINDS = "'trial_ending_3d', 'trial_ending_1d', 'trial_ended', 'wizard_reminder'"
NEW_KINDS = f"{OLD_KINDS}, 'quota_80'"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(
        "tenant_notifications_kind", "tenant_notifications", f"kind IN ({NEW_KINDS})"
    )


def downgrade() -> None:
    # Rows of the new kind would violate the narrower constraint, so they go
    # first. Nothing is lost that matters: the notice has already been sent,
    # and the row only exists to stop it being sent twice.
    op.execute("DELETE FROM tenant_notifications WHERE kind = 'quota_80'")
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(
        "tenant_notifications_kind", "tenant_notifications", f"kind IN ({OLD_KINDS})"
    )
