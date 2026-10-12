"""The sandbox: talking to your own agent before it is live (TASK-015).

This is where a customer decides the product is worth paying for, so two
decisions in `plan.md` and Clarification 002 shape everything here:

**It is free and unmetered.** Sandbox messages never touch
`tenants.ai_messages_used`. Charging for the place people evaluate the product
suppresses exactly the experimentation that leads to a sale - Zaia caps its
trial at ten messages and the research calls that an antipattern to avoid.

**It runs the real agent, not a simulation.** Same turn pipeline, same prompt,
same tools, same tables; only the channel differs. A sandbox that answered with
canned text would demo something the customer will never receive, and the
divergence would be discovered on the day the number connects.

The cost that comes with "free and unmetered" is the LLM bill, which is real.
The guard is a per-tenant rate limit rather than a quota, because a limit
throttles abuse without ever telling a legitimate customer they have run out.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline, TurnResult
from app.models.conversation import Conversation, Message
from app.providers.llm.base import LLMProvider
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.services.activation import build_checklist
from app.services.turn_outbound import SandboxOutbound

SANDBOX_CHANNEL = "sandbox"


class SandboxNotReady(RuntimeError):
    """The activation checklist has not reached the sandbox threshold."""


class ConversationNotFound(LookupError):
    """No such sandbox conversation for this tenant."""


@dataclass(frozen=True)
class SandboxTurn:
    """One exchange: what the customer said, what the agent answered, and why."""

    customer_message: Message
    # Everything the agent sent, in the order the customer saw it: the files and
    # options the tools put on the screen, then the text.
    outbound: list[Message]
    result: TurnResult

    @property
    def agent_message(self) -> Message | None:
        """The first text bubble of the answer; None if the turn had none."""
        texts = self.result.text_messages
        return texts[0] if texts else None


class SandboxService:
    """Sandbox conversations for one tenant."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        llm: LLMProvider,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        self._conversations = ConversationRepository(session, tenant_id)
        self._messages = MessageRepository(session, tenant_id)

    def start(self, *, user: object, started_by: uuid.UUID) -> Conversation:
        """Open a conversation, refusing if the wizard is not far enough along.

        Guarded by the same checklist the panel shows, so "start chatting" is
        never offered next to a call that refuses.
        """
        checklist = build_checklist(self._session, tenant_id=self._tenant_id, user=user)  # type: ignore[arg-type]
        if not checklist.sandbox_ready:
            raise SandboxNotReady("the activation checklist is not complete enough")

        conversation = self._conversations.add(
            channel=SANDBOX_CHANNEL, started_by=started_by, status="open"
        )
        self._session.commit()
        return conversation

    def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.channel != SANDBOX_CHANNEL:
            # A real WhatsApp conversation is not reachable through the
            # sandbox endpoints, even by its owner: different rules apply to
            # it, and blurring the two is how a test message reaches a
            # customer.
            return None
        return conversation

    def history(
        self, conversation_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        if self.get(conversation_id) is None:
            raise ConversationNotFound(str(conversation_id))
        total = self._messages.count(Message.conversation_id == conversation_id)
        rows = self._messages.list_for_conversation(conversation_id)
        page = list(rows)[offset : offset + limit]
        return page, total

    async def send(self, conversation_id: uuid.UUID, *, content: str) -> SandboxTurn:
        """Record the customer's message, answer it, record the answer.

        The customer's message is persisted **before** the model is called, so
        a provider failure still leaves the conversation showing what was
        asked. Losing the question along with the answer would make the
        transcript lie about what happened.
        """
        if self.get(conversation_id) is None:
            raise ConversationNotFound(str(conversation_id))

        customer_message = self._messages.append(
            conversation_id=conversation_id,
            direction="inbound",
            author="customer",
            content=content,
        )
        self._session.commit()

        pipeline = TurnPipeline(
            self._session,
            self._tenant_id,
            llm=self._llm,
            outbound=SandboxOutbound(self._session, self._tenant_id, conversation_id),
            sandbox=True,
            queue_text=False,
        )
        result = await pipeline.run(conversation_id, InboundTurn(text=content))

        # `tenants.ai_messages_used` is deliberately untouched: the sandbox is
        # free and unmetered, during the trial and after (Clarification 002).
        # The real channel meters through `app.services.trial.record_ai_message`
        # and gates on `agent_may_reply`; Feature 002 calls both from the
        # webhook. Neither belongs here, and neither belongs inside `AgentCore`
        # either - putting them there would meter the sandbox by accident.
        self._session.commit()

        return SandboxTurn(
            customer_message=customer_message,
            outbound=result.messages,
            result=result,
        )
