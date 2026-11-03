"""The platform is called Z-BOT: the assistant's default name (2026-11-03).

Revision ID: 0077
Revises: 0076

Giannier, 2026-11-03: the product is presented as Z-BOT, with Zentek360's colours. The panel, the
emails and the public chat say Z-BOT; this moves the database default of `agent_configs.agent_name`
with them, for a business created without a name. Businesses that already exist keep the name they
have: it is their data, and they change it in the panel.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0077"
down_revision: str | Sequence[str] | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("agent_configs", "agent_name", server_default="Z-BOT")


def downgrade() -> None:
    op.alter_column("agent_configs", "agent_name", server_default="Leo")
