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

Feature 004 (C-19) adds the rest of what makes it a simulator rather than a chat
box: the customer is a synthetic contact (`sandbox:{user_id}`), what the tools
send (a file, buttons, a list) is recorded as typed outbound messages that the
panel draws, and nothing ever reaches the provider.

The cost that comes with "free and unmetered" is the LLM bill, which is real.
The guard is a per-tenant rate limit rather than a quota, because a limit
throttles abuse without ever telling a legitimate customer they have run out.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline, TurnResult
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.feedback import MOTIVO_MAX, SandboxFeedback
from app.orders.proofs import InboundImage, ProofPipeline, ReceivedImage
from app.process.inbound import interactive_of, stored_image_of
from app.providers.llm.base import LLMProvider
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.whatsapp.base import InteractiveReply
from app.repositories.channel import ContactRepository
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.feedback import SandboxFeedbackRepository
from app.repositories.sandbox_reset import SandboxResetRepository
from app.services.activation import build_checklist
from app.services.inbound_media import InboundImageReader
from app.services.turn_outbound import SandboxOutbound

logger = logging.getLogger(__name__)

SANDBOX_CHANNEL = "sandbox"

# The identity the simulator's contact carries (C-19): one per panel user, so two
# people trying the same business do not share a customer.
SANDBOX_CONTACT_PREFIX = "sandbox:"


class SandboxNotReady(RuntimeError):
    """The activation checklist has not reached the sandbox threshold."""


class ConversationNotFound(LookupError):
    """No such sandbox conversation for this tenant."""


class NotARating(ValueError):
    """Only a reply of the assistant can be rated."""


# The copy of the question and the answer kept with a rating.
FEEDBACK_TEXT_MAX = 4000


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
        storage: StorageProvider | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        # Where the customer's photo is kept so the turn can read it as a payment
        # receipt; without it an image is only a line of context.
        self._storage = storage
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

        contact = self._contact(f"{SANDBOX_CONTACT_PREFIX}{started_by}")
        conversation = self._conversations.add(
            channel=SANDBOX_CHANNEL, started_by=started_by, contact_id=contact.id, status="open"
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

    def outbound_after(
        self, conversation_id: uuid.UUID, after_id: uuid.UUID | None
    ) -> list[Message]:
        """What the agent said after one message: the asynchronous outbounds.

        The confirmation or rejection of a payment, a handover and the follow-ups
        are written as outbound messages with no customer message to answer
        (C-19). The simulator polls this while it is open.
        """
        if self.get(conversation_id) is None:
            raise ConversationNotFound(str(conversation_id))
        return list(self._messages.outbound_after(conversation_id, after_id))

    def rate(
        self,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        *,
        rating: int,
        motivo: str | None,
        by: uuid.UUID | None,
    ) -> SandboxFeedback:
        """Thumbs up or down on one reply of the assistant (Feature 018, HU-06).

        Rating again replaces the earlier one. The customer's message the reply
        answered and the reply itself are copied, so the rating survives
        "Reiniciar chat".
        """
        if self.get(conversation_id) is None:
            raise ConversationNotFound(str(conversation_id))
        transcript = list(self._messages.list_for_conversation(conversation_id))
        index = next((i for i, m in enumerate(transcript) if m.id == message_id), None)
        if index is None:
            raise ConversationNotFound(str(message_id))
        reply = transcript[index]
        if reply.direction != "outbound" or reply.author != "agent":
            raise NotARating(str(message_id))
        question = next(
            (m.content for m in reversed(transcript[:index]) if m.direction == "inbound"), None
        )
        repository = SandboxFeedbackRepository(self._session, self._tenant_id)
        row = repository.by_message(message_id)
        if row is None:
            row = repository.add(
                conversation_id=conversation_id,
                message_id=message_id,
                rating=rating,
                created_by=by,
                respuesta="",
            )
        row.rating = rating
        row.motivo = (motivo or "").strip()[:MOTIVO_MAX] or None
        row.pregunta = (question or "")[:FEEDBACK_TEXT_MAX] or None
        row.respuesta = (reply.content or "")[:FEEDBACK_TEXT_MAX]
        self._session.commit()
        return row

    def unrate(self, conversation_id: uuid.UUID, message_id: uuid.UUID) -> bool:
        if self.get(conversation_id) is None:
            raise ConversationNotFound(str(conversation_id))
        repository = SandboxFeedbackRepository(self._session, self._tenant_id)
        row = repository.by_message(message_id)
        if row is None or row.conversation_id != conversation_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def ratings(self, message_ids: list[uuid.UUID]) -> dict[uuid.UUID, SandboxFeedback]:
        return SandboxFeedbackRepository(self._session, self._tenant_id).for_messages(message_ids)

    def reset(self, conversation_id: uuid.UUID) -> None:
        """Forget everything: the state, the messages and the sandbox orders."""
        if self.get(conversation_id) is None:
            raise ConversationNotFound(str(conversation_id))
        SandboxResetRepository(self._session, self._tenant_id).wipe(conversation_id)
        self._session.commit()

    async def send(
        self,
        conversation_id: uuid.UUID,
        *,
        content: str,
        interactive: InteractiveReply | None = None,
        image: ReceivedImage | None = None,
        drafts: Sequence[uuid.UUID] = (),
    ) -> SandboxTurn:
        """Record the customer's message, answer it, record the answer.

        The customer's message is persisted **before** the model is called, so
        a provider failure still leaves the conversation showing what was
        asked. Losing the question along with the answer would make the
        transcript lie about what happened.

        A tapped button is stored the way the real channel stores it: the title
        the customer saw is the text, and the id travels in the payload.
        """
        conversation = self.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(str(conversation_id))
        self._ensure_contact(conversation)
        payload = self._payload(interactive, image)

        customer_message = self._messages.append(
            conversation_id=conversation_id,
            direction="inbound",
            author="customer",
            content=content,
            msg_type="image" if image is not None else "text",
            payload=payload,
        )
        self._session.commit()

        pipeline = TurnPipeline(
            self._session,
            self._tenant_id,
            llm=self._llm,
            outbound=SandboxOutbound(self._session, self._tenant_id, conversation_id),
            sandbox=True,
            queue_text=False,
            proofs=ProofPipeline(
                self._session,
                self._tenant_id,
                llm=self._llm,
                storage=self._storage,
            ),
            images=InboundImageReader(self._session, self._tenant_id, storage=self._storage),
            situation_drafts=drafts,
        )
        tap = interactive_of(customer_message.payload)
        stored = stored_image_of(customer_message.payload)
        result = await pipeline.run(
            conversation_id,
            InboundTurn(
                text="" if image is not None else content,
                interactive=(tap,) if tap is not None else (),
                image=image is not None,
                images=(
                    (InboundImage(message_id=customer_message.id, stored=stored),)
                    if stored is not None
                    else ()
                ),
            ),
        )

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

    def _payload(
        self, interactive: InteractiveReply | None, image: ReceivedImage | None
    ) -> dict[str, object] | None:
        """What the customer's row remembers: the tapped option, or where the photo is.

        The photo goes to private storage the way a real one would sit with the
        provider: the turn fetches it from there when it expects a receipt.
        """
        if interactive is not None:
            return {
                "interactive": {
                    "id": interactive.id,
                    "title": interactive.title,
                    "description": interactive.description,
                }
            }
        if image is not None and self._storage is not None:
            extension = {"image/png": "png", "image/webp": "webp"}.get(image.mime_type, "jpg")
            key = f"tenants/{self._tenant_id}/sandbox/{uuid.uuid4()}.{extension}"
            try:
                self._storage.put(key, image.data, content_type=image.mime_type)
            except StorageError:
                # The turn goes on: the agent hears "[El cliente envió una
                # imagen]" instead of reading it. A storage outage must not
                # turn the simulator into "no connection".
                logger.exception("a simulator image could not be stored")
                return None
            return {"image": {"storage_key": key, "mime_type": image.mime_type}}
        return None

    # ------------------------------------------------------------ the contact

    def _contact(self, external_user_id: str) -> Contact:
        """The simulator's customer: no phone, a synthetic id (C-19)."""
        return ContactRepository(self._session, self._tenant_id).upsert(
            external_user_id=external_user_id
        )

    def _ensure_contact(self, conversation: Conversation) -> None:
        """Give a conversation opened before the simulator had a contact one."""
        if conversation.contact_id is not None:
            return
        owner = conversation.started_by or conversation.id
        contact = self._contact(f"{SANDBOX_CONTACT_PREFIX}{owner}")
        self._conversations.update(conversation.id, contact_id=contact.id)
