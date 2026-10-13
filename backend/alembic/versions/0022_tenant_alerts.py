"""tenant_notifications: events that repeat, with a payload

Feature 004 (Motor de ventas), TASK-431. `conciliacion.md` 3.1 and 6 recorded that the
table could not hold what `contracts/api.md` section 10 asks of it: it was built for
notices sent *once* per tenant (the trial warnings, the wizard reminder, the 80% quota
notice), so its primary key was `(tenant_id, kind)` and a second row of the same kind
was an error by design.

The sales engine needs the opposite: a receipt arrived, a customer chose cash on
delivery, a conversation was handed to a person, the customer could not be reached,
the model went down. Each of those happens many times and each one is its own row,
with the order or the conversation it is about.

What changes, and what does not:

* The primary key becomes a new `id`. The one-shot guarantee does not go away: a
  **partial unique index** over `(tenant_id, kind)` for the five original kinds keeps
  `claim_notification` working exactly as before (a duplicate still raises
  `IntegrityError`), while the five new kinds are free to repeat.
* `payload` (jsonb) carries what the notice is about (order number, conversation id,
  motive...). It never holds personal data beyond what the business already sees.
* The kind CHECK gains `nuevo_comprobante`, `pedido_contra_entrega`, `derivacion`,
  `ventana_cerrada` and `llm_caido`.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_tenant_notifications_tenant_notifications_kind"
ONE_SHOT = ("trial_ending_3d", "trial_ending_1d", "trial_ended", "wizard_reminder", "quota_80")
EVENTS = (
    "nuevo_comprobante",
    "pedido_contra_entrega",
    "derivacion",
    "ventana_cerrada",
    "llm_caido",
)


def _in_list(values: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "tenant_notifications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    )
    op.add_column(
        "tenant_notifications",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.drop_constraint(op.f("pk_tenant_notifications"), "tenant_notifications", type_="primary")
    op.create_primary_key(op.f("pk_tenant_notifications"), "tenant_notifications", ["id"])
    op.create_index(
        op.f("uq_tenant_notifications_tenant_id_kind"),
        "tenant_notifications",
        ["tenant_id", "kind"],
        unique=True,
        postgresql_where=sa.text(_in_list(ONE_SHOT)),
    )
    op.create_index(
        "ix_tenant_notifications_tenant_id_kind_created_at",
        "tenant_notifications",
        ["tenant_id", "kind", sa.text("created_at DESC")],
    )
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(
        "tenant_notifications_kind", "tenant_notifications", _in_list((*ONE_SHOT, *EVENTS))
    )


def downgrade() -> None:
    # The events have no place in the old shape (one row per kind), and a notice is
    # a record of something already told: nothing else depends on them.
    op.execute(
        "DELETE FROM tenant_notifications WHERE kind NOT IN ("
        + ", ".join(f"'{value}'" for value in ONE_SHOT)
        + ")"
    )
    op.drop_constraint(op.f(CONSTRAINT), "tenant_notifications", type_="check")
    op.create_check_constraint(
        "tenant_notifications_kind", "tenant_notifications", _in_list(ONE_SHOT)
    )
    op.drop_index("ix_tenant_notifications_tenant_id_kind_created_at", "tenant_notifications")
    op.drop_index(op.f("uq_tenant_notifications_tenant_id_kind"), "tenant_notifications")
    op.drop_constraint(op.f("pk_tenant_notifications"), "tenant_notifications", type_="primary")
    op.drop_column("tenant_notifications", "payload")
    op.drop_column("tenant_notifications", "id")
    op.create_primary_key(
        op.f("pk_tenant_notifications"), "tenant_notifications", ["tenant_id", "kind"]
    )
