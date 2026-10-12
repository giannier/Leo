"""Wiping a simulator conversation (Feature 004, C-19).

"Reiniciar conversación" deletes the state, the messages and the sandbox orders of
one conversation so the owner can walk the sales process again from the greeting.
It deletes in code, in order, because the schema has no `ON DELETE CASCADE` from
`conversations` on purpose (`conciliacion.md` 8.0): an unscoped `DELETE` on a real
conversation must never take the sales history with it.

That is also why this is the only place that deletes these rows, and why it refuses
anything that is not a sandbox conversation of the tenant: every statement carries
the tenant, and the orders are only removed if they are sandbox orders.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.channel import MessageStatusEvent
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent, ConversationState
from app.models.orders import Order, PaymentProof
from app.repositories.base import TenantScopeError


class SandboxResetRepository:
    """Deletes the working data of one sandbox conversation of one tenant."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    def wipe(self, conversation_id: uuid.UUID) -> None:
        """Remove the state, stage events, messages, traces and sandbox orders."""
        tenant = self._tenant_id
        channel = self._session.execute(
            select(Conversation.channel).where(
                Conversation.id == conversation_id, Conversation.tenant_id == tenant
            )
        ).scalar_one_or_none()
        if channel != "sandbox":
            raise TenantScopeError(
                f"Conversation {conversation_id!s} is not a sandbox conversation of "
                f"tenant {tenant!s}"
            )

        # Proofs hang from orders, and from messages (SET NULL): they go first.
        self._session.execute(
            delete(PaymentProof).where(
                PaymentProof.tenant_id == tenant, PaymentProof.conversation_id == conversation_id
            )
        )
        # The order items go with their order (the one cascade the schema has).
        self._session.execute(
            delete(Order).where(
                Order.tenant_id == tenant,
                Order.conversation_id == conversation_id,
                Order.is_sandbox.is_(True),
            )
        )

        message_ids = select(Message.id).where(
            Message.tenant_id == tenant, Message.conversation_id == conversation_id
        )
        self._session.execute(
            delete(AgentTrace).where(
                AgentTrace.tenant_id == tenant, AgentTrace.message_id.in_(message_ids)
            )
        )
        self._session.execute(
            delete(MessageStatusEvent).where(MessageStatusEvent.message_id.in_(message_ids))
        )
        self._session.execute(
            delete(Message).where(
                Message.tenant_id == tenant, Message.conversation_id == conversation_id
            )
        )
        self._session.execute(
            delete(ConversationStageEvent).where(
                ConversationStageEvent.tenant_id == tenant,
                ConversationStageEvent.conversation_id == conversation_id,
            )
        )
        self._session.execute(
            delete(ConversationState).where(
                ConversationState.tenant_id == tenant,
                ConversationState.conversation_id == conversation_id,
            )
        )
        self._session.expire_all()
