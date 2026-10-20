"""The business's greeting may be up to 400 characters

120 was too short for a real welcome message with the services on offer.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_agent_configs_saludo_length"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "agent_configs", type_="check")
    op.create_check_constraint(CONSTRAINT, "agent_configs", "char_length(saludo) <= 400")


def downgrade() -> None:
    # Greetings longer than the old cap are cut so the old constraint can be restored.
    op.execute(
        "UPDATE agent_configs SET saludo = left(saludo, 120) WHERE char_length(saludo) > 120"
    )
    op.drop_constraint(CONSTRAINT, "agent_configs", type_="check")
    op.create_check_constraint(CONSTRAINT, "agent_configs", "char_length(saludo) <= 120")
