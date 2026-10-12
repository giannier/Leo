"""contacts with BSUID and customer data, richer agent traces

Feature 004 (Motor de ventas), TASK-408.

* `contacts.external_user_id` holds Meta's business-scoped user id (BSUID), so a
  person who writes without sharing a phone number is still one contact.
  `phone_e164` becomes nullable and a CHECK requires at least one of the two.
* `contacts.custom_data` keeps what the customer told the assistant for an order
  (`datos_requeridos`: DNI, email, address...).
* `agent_traces` records the stage, funnel column, tools offered, outbound count
  and model tier of each turn.

Adaptations to `data-model.md` (see `conciliacion.md`, "Adaptaciones"):

* The phone column is `phone_e164`, not `phone`, and no `wa_id` exists
  (`conciliacion.md`, 3.1).
* Index and CHECK names follow `NAMING_CONVENTION`: `uq_contacts_tenant_id_external_user_id`
  and `ck_contacts_identity`.
* The downgrade refuses to run while a contact without a phone exists: putting
  `NOT NULL` back would either fail halfway or have to delete a customer.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("external_user_id", sa.Text(), nullable=True))
    op.add_column(
        "contacts",
        sa.Column(
            "custom_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.alter_column("contacts", "phone_e164", existing_type=sa.Text(), nullable=True)
    op.create_index(
        "uq_contacts_tenant_id_external_user_id",
        "contacts",
        ["tenant_id", "external_user_id"],
        unique=True,
        postgresql_where=sa.text("external_user_id IS NOT NULL"),
    )
    op.create_check_constraint(
        op.f("ck_contacts_identity"),
        "contacts",
        "phone_e164 IS NOT NULL OR external_user_id IS NOT NULL",
    )

    op.add_column("agent_traces", sa.Column("stage_key", sa.Text(), nullable=True))
    op.add_column("agent_traces", sa.Column("funnel_code", sa.Text(), nullable=True))
    op.add_column("agent_traces", sa.Column("tools_enabled", sa.ARRAY(sa.Text()), nullable=True))
    op.add_column("agent_traces", sa.Column("outbound_count", sa.SmallInteger(), nullable=True))
    op.add_column("agent_traces", sa.Column("model_tier", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_traces", "model_tier")
    op.drop_column("agent_traces", "outbound_count")
    op.drop_column("agent_traces", "tools_enabled")
    op.drop_column("agent_traces", "funnel_code")
    op.drop_column("agent_traces", "stage_key")

    without_phone = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM contacts WHERE phone_e164 IS NULL"))
        .scalar_one()
    )
    if without_phone:
        raise RuntimeError(
            f"{without_phone} contact(s) have no phone number (BSUID only); "
            "restoring NOT NULL on contacts.phone_e164 would lose them. "
            "Resolve those contacts first."
        )
    op.drop_constraint(op.f("ck_contacts_identity"), "contacts", type_="check")
    op.drop_index("uq_contacts_tenant_id_external_user_id", table_name="contacts")
    op.alter_column("contacts", "phone_e164", existing_type=sa.Text(), nullable=False)
    op.drop_column("contacts", "custom_data")
    op.drop_column("contacts", "external_user_id")
