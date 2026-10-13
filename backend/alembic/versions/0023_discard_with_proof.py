"""Discarding an order the customer already paid: the proof is voided, a refund is owed

Feature 004 (Motor de ventas), Milestone B review, finding E-2. Discarding an order
used to change only the order: a waiting receipt stayed `por_verificar` forever, an
approved one stayed approved, and nobody knew a customer who had paid was owed their
money back.

* `payment_proofs.estado` gains `anulado`: the proof of an order that was discarded.
* `orders.devolucion_pendiente` marks an order that was confirmed (or in progress)
  when it was discarded: the business took the money and owes it back. The Pedidos
  screen shows it to the operator.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROOF_STATES_BEFORE = "estado IN ('por_verificar', 'aprobado', 'rechazado')"
_PROOF_STATES_AFTER = "estado IN ('por_verificar', 'aprobado', 'rechazado', 'anulado')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_payment_proofs_estado"), "payment_proofs", type_="check")
    op.create_check_constraint(
        op.f("ck_payment_proofs_estado"), "payment_proofs", _PROOF_STATES_AFTER
    )
    op.add_column(
        "orders",
        sa.Column("devolucion_pendiente", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("orders", "devolucion_pendiente")
    # A voided proof has no state to go back to: it returns to rejected.
    op.execute("UPDATE payment_proofs SET estado = 'rechazado' WHERE estado = 'anulado'")
    op.drop_constraint(op.f("ck_payment_proofs_estado"), "payment_proofs", type_="check")
    op.create_check_constraint(
        op.f("ck_payment_proofs_estado"), "payment_proofs", _PROOF_STATES_BEFORE
    )
