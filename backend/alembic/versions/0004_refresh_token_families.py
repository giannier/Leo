"""Group rotating refresh tokens into families.

Rotation turns one sign-in into a chain of refresh-token rows, and reuse of
an already-revoked link is the only signal that a token was stolen. Acting on
that signal means revoking the compromised chain - which requires knowing
which rows belong to it. `family_id` is that link: minted at login, copied to
every successor, indexed because revoking a family writes to every row that
shares the value.

Added in three steps rather than one so it is safe on a table that already
has rows: add nullable, give every existing row its own family (its `id`,
which is unique by construction), then require the column. Existing sessions
therefore keep working and are simply each a family of one.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-30 23:42:10.118904

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("refresh_tokens", sa.Column("family_id", sa.UUID(), nullable=True))
    op.execute(sa.text("UPDATE refresh_tokens SET family_id = id WHERE family_id IS NULL"))
    op.alter_column("refresh_tokens", "family_id", existing_type=sa.UUID(), nullable=False)
    op.create_index(
        op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "family_id")
