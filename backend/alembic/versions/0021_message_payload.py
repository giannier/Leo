"""a typed payload on messages, and the message types the sales engine sends

Feature 004 (Motor de ventas), TASK-421.

C-11 gives every outbound message its own row, and the sales engine sends more
than text: a library file, reply buttons, a list. `conciliacion.md` (3.1 and 5.2)
already recorded that `messages` could not hold them. This adds:

* `messages.payload` (jsonb, nullable): the structure of a typed message. For an
  outbound it is the buttons, the list rows or the library file that was sent;
  for an inbound it is the tapped option (`interactive`) and the ad the customer
  came from (`referral`), which the turn pipeline reads after the debounce window
  (the webhook event is gone by then). `content` keeps the readable text, so the
  transcript, the agent's history and the inbox work unchanged.
* `interactive` and `video` as message types (`MESSAGE_TYPES`).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = (
    "text",
    "image",
    "document",
    "audio",
    "sticker",
    "location",
    "contact",
    "unsupported",
)
_AFTER = (*_BEFORE, "video", "interactive")


def _in_list(values: Sequence[str]) -> str:
    return "msg_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.create_check_constraint("messages_msg_type", "messages", _in_list(_AFTER))


def downgrade() -> None:
    # A typed row has no equivalent in the old vocabulary; refusing is safer than
    # rewriting history to `text` and losing what was sent.
    bind = op.get_bind()
    typed = bind.execute(
        sa.text("SELECT count(*) FROM messages WHERE msg_type IN ('video', 'interactive')")
    ).scalar_one()
    if typed:
        raise RuntimeError(
            "cannot downgrade: messages of type 'video' or 'interactive' exist and "
            "would violate the previous constraint"
        )
    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.create_check_constraint("messages_msg_type", "messages", _in_list(_BEFORE))
    op.drop_column("messages", "payload")
