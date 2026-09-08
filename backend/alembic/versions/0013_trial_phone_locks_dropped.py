"""drop trial_phone_locks: the rule is a question, not a stored claim

Clarification 005 says a verified contact phone may not sit in more than one
tenant "en trial simultaneamente". `trial_phone_locks` implemented that as a
row taken at registration - and never given back. A business that started a
trial, walked away and came back could never register that number again,
because the only thing that would have freed it was a `DELETE` nobody wrote.

The condition spans three tables (`tenants.status`, the owner row in
`tenant_users`, `users.phone_e164`), and PostgreSQL does not allow subqueries
in index expressions, which is why the lock table existed at all. So the rule
moves out of the schema and into `TrialPhoneRepository`: a query over the
tenants that are in trial right now, serialised per number by a
transaction-scoped advisory lock. A tenant that leaves trial stops holding
its phone by definition - there is nothing left to release, and therefore
nothing left to leak.

Nothing reads this table any more, so dropping it loses no behaviour. The
downgrade rebuilds it from live state rather than from the rows this drop
destroys: what the old code needed was one row per phone held by a live
trial, and that is exactly what the query below produces.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("trial_phone_locks")


def downgrade() -> None:
    op.create_table(
        "trial_phone_locks",
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_trial_phone_locks_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("phone_e164", name=op.f("pk_trial_phone_locks")),
    )
    # Repopulated from the tenants that still hold a number at this moment - in
    # trial or paused, matching `PHONE_HOLDING_STATUSES`. Not `trial` alone: a
    # paused tenant is a trial that expired, and dropping those rows on the way
    # down would hand every one of those numbers back the instant the rollback
    # finished. So the
    # restored code wakes up holding the locks it would have held.
    #
    # `DISTINCT ON` is load-bearing rather than tidy: the primary key allows
    # one row per number, and two live tenants *can* legitimately share one -
    # an admin extending a paused tenant back into trial does exactly that.
    # Without it the downgrade would abort on real data. Oldest trial wins,
    # which matches how the lock behaved when it was first taken.
    op.execute(
        sa.text(
            """
            INSERT INTO trial_phone_locks (phone_e164, tenant_id)
            SELECT DISTINCT ON (u.phone_e164) u.phone_e164, t.id
            FROM tenants t
            JOIN tenant_users tu
              ON tu.tenant_id = t.id AND tu.role = 'owner' AND tu.status = 'active'
            JOIN users u ON u.id = tu.user_id
            WHERE t.status IN ('trial', 'paused') AND u.phone_e164 IS NOT NULL
            ORDER BY u.phone_e164, t.created_at
            """
        )
    )
