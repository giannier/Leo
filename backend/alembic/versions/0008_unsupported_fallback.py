"""agent_configs.unsupported_fallback: what to say to a voice note

Feature 002, C-001. A customer sends an audio, a video or a location and this
version cannot read it. The message is still stored and still counts as an
interaction, but the agent has to answer *something* - and what that something
is differs by business: a bakery asks for it in writing, a clinic would rather
hand straight over to a person.

Nullable, with the built-in used when it is unset, so no tenant has to
configure anything for the channel to behave sensibly on day one.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_configs", sa.Column("unsupported_fallback", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_configs", "unsupported_fallback")
