"""contacts.posible_duplicado_de: two rows of one person, marked instead of merged

Feature 004 (Motor de ventas), Milestone B, decision 12. When a message carries a
BSUID that belongs to one contact and a phone that belongs to another, the two rows
are the same person. `ContactRepository.upsert` does not merge them (merging would
move conversations and orders); it now marks each one with the other's id, so the
operator sees that the customer may be duplicated.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("posible_duplicado_de", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_contacts_posible_duplicado_de_contacts"),
        "contacts",
        "contacts",
        ["posible_duplicado_de"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_contacts_posible_duplicado_de_contacts"), "contacts", type_="foreignkey"
    )
    op.drop_column("contacts", "posible_duplicado_de")
