"""Partial index for retiring a user's live email tokens.

Registration and every resend issue a token, and issuing one first retires
the user's still-usable tokens for that purpose. That is a lookup by
(user_id, purpose) restricted to unconsumed rows, and `email_tokens` had no
index on `user_id` at all - only the unique index on `token_hash`, which
this access path cannot use. The table is append-only, so the scan would get
slower with every account created.

Partial on `consumed_at IS NULL`: spent tokens are never the target of this
query, and leaving them out keeps the index roughly the size of the number
of pending verifications rather than the whole history.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30 22:14:32.426311

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_email_tokens_user_id_purpose_active",
        "email_tokens",
        ["user_id", "purpose"],
        unique=False,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_email_tokens_user_id_purpose_active",
        table_name="email_tokens",
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
