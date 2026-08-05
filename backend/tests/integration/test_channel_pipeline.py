"""One reply per burst, and the cases where there is no reply (TASK-204).

The feature's own verification is here: five messages in a row produce one
answer, not five. That is RF-005, and it is not a nicety - three answers to
one question reads like a broken robot, costs three model calls, and is the
first thing a customer notices.

The other half is every reason the agent stays quiet - it is off, the trial is
over, the tenant is paused, the message was a voice note - and the rule that
none of those may lose the message. It stays in the thread for the owner to
see, because the person who sent it has no idea any of that happened.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.providers.llm import ChatResult, MockLLMProvider
from app.services.channel import (
    DEFAULT_UNSUPPORTED_FALLBACK,
    ConversationResponder,
    debounce_key,
)
from app.workers.channel import ANSWER_JOB, schedule_answer
from tests.unit.test_locks import FakeStore


class RecordingQueue:
    """A `JobQueue` that remembers instead of reaching Redis."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...], float | None]] = []

    async def enqueue(
        self,
        function: str,
        *args: object,
        defer_seconds: float | None = None,
        job_id: str | None = None,
    ) -> str | None:
        self.enqueued.append((function, args, defer_seconds))
        return f"job-{len(self.enqueued)}"


def _tenant(db: Session, *, active: bool = True, status: str = "trial") -> Tenant:
    """A tenant whose agent is switched on and whose trial is alive."""
    plan = db.get(Plan, "trial")
    assert plan is not None
    tenant = Tenant(
        name=f"Panaderia {uuid.uuid4().hex[:8]}",
        status=status,
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(tenant)
    db.flush()
    db.add(AgentConfig(tenant_id=tenant.id, is_active=active))
    db.flush()
    return tenant


def _thread(db: Session, tenant: Tenant) -> Conversation:
    contact = Contact(
        tenant_id=tenant.id,
        phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}",
        last_inbound_at=datetime.now(UTC),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(conversation)
    db.flush()
    return conversation


def _said(db: Session, conversation: Conversation, text: str, *, msg_type: str = "text") -> None:
    db.add(
        Message(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            content=text,
            msg_type=msg_type,
            provider_message_id=f"wamid.{uuid.uuid4().hex}",
        )
    )
    db.flush()


def _llm(*replies: str) -> MockLLMProvider:
    provider = MockLLMProvider()
    for reply in replies:
        provider.queue(ChatResult(content=reply))
    return provider


class TestABurstGetsOneAnswer:
    async def test_five_messages_produce_one_reply(self, db: Session) -> None:
        """The feature's own verification for RF-005."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        for text in ["hola", "buenas", "estan?", "tienen pan", "integral"]:
            _said(db, conversation, text)
        llm = _llm("Buenas! Si, tenemos pan integral.")

        answer = await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        assert answer is not None
        outbound = (
            db.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "outbound",
                )
            )
            .scalars()
            .all()
        )
        assert len(outbound) == 1

    async def test_the_agent_is_asked_the_whole_burst_at_once(self, db: Session) -> None:
        """Answering only the last line loses the question. "tienen pan" /
        "integral" is one request, and the second word alone means nothing."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "tienen pan")
        _said(db, conversation, "integral")
        llm = _llm("Si, tenemos.")

        await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        asked = llm.chat_calls[-1].messages[-1].content
        assert "tienen pan" in asked
        assert "integral" in asked

    async def test_the_burst_is_what_came_after_the_last_reply(self, db: Session) -> None:
        """Not the whole transcript. An already-answered question re-sent to
        the model is a second answer to something the customer settled."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "primera pregunta")
        await ConversationResponder(db, tenant.id, llm=_llm("primera respuesta")).respond(
            conversation.id
        )
        _said(db, conversation, "segunda pregunta")
        llm = _llm("segunda respuesta")

        await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        asked = llm.chat_calls[-1].messages[-1].content
        assert "segunda pregunta" in asked
        assert "primera pregunta" not in asked

    async def test_a_thread_with_nothing_new_is_not_answered(self, db: Session) -> None:
        """A job that ran twice, or a human who replied from the inbox first."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "hola")
        await ConversationResponder(db, tenant.id, llm=_llm("hola!")).respond(conversation.id)

        second = await ConversationResponder(db, tenant.id, llm=_llm("otra vez")).respond(
            conversation.id
        )

        assert second is None


class TestTheReplyIsRecordedBeforeItIsSent:
    async def test_it_is_stored_queued(self, db: Session) -> None:
        """Written down first, handed to the provider second. A send that
        fails then leaves a record of what the business meant to say instead
        of a silence nobody can explain."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "hola")

        answer = await ConversationResponder(db, tenant.id, llm=_llm("Buenas!")).respond(
            conversation.id
        )

        assert answer is not None
        assert answer.delivery_status == "queued"
        assert answer.direction == "outbound"
        assert answer.author == "agent"

    async def test_the_reply_is_metered(self, db: Session) -> None:
        """RF-004. The model bill is incurred whether or not the send works,
        and an unmetered channel hands out free messages."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "hola")
        before = tenant.ai_messages_used

        await ConversationResponder(db, tenant.id, llm=_llm("Buenas!")).respond(conversation.id)

        db.refresh(tenant)
        assert tenant.ai_messages_used == before + 1


class TestWhenTheAgentStaysQuiet:
    async def test_a_switched_off_agent_answers_nothing(self, db: Session) -> None:
        tenant = _tenant(db, active=False)
        conversation = _thread(db, tenant)
        _said(db, conversation, "hola")

        assert (
            await ConversationResponder(db, tenant.id, llm=_llm("no deberia")).respond(
                conversation.id
            )
            is None
        )

    async def test_a_paused_tenant_answers_nothing(self, db: Session) -> None:
        tenant = _tenant(db, status="paused")
        conversation = _thread(db, tenant)
        _said(db, conversation, "hola")

        assert (
            await ConversationResponder(db, tenant.id, llm=_llm("no deberia")).respond(
                conversation.id
            )
            is None
        )

    async def test_the_message_is_still_there_for_the_owner(self, db: Session) -> None:
        """HU-01. The customer has no idea the agent is off and will conclude
        the business ignored them - so the owner has to be able to see it."""
        tenant = _tenant(db, active=False)
        conversation = _thread(db, tenant)
        _said(db, conversation, "tienen pan?")

        await ConversationResponder(db, tenant.id, llm=_llm("x")).respond(conversation.id)

        stored = (
            db.execute(select(Message).where(Message.conversation_id == conversation.id))
            .scalars()
            .all()
        )
        assert [message.content for message in stored] == ["tienen pan?"]

    async def test_a_quiet_turn_costs_nothing(self, db: Session) -> None:
        tenant = _tenant(db, active=False)
        conversation = _thread(db, tenant)
        _said(db, conversation, "hola")
        llm = _llm("no deberia")

        await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        assert llm.chat_calls == []
        db.refresh(tenant)
        assert tenant.ai_messages_used == 0


class TestAMessageThisVersionCannotRead:
    async def test_it_gets_the_built_in_fallback(self, db: Session) -> None:
        """C-001. The model is not asked: it cannot see a voice note, and
        showing it `[audio]` teaches it to talk about a placeholder."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "[audio]", msg_type="unsupported")
        llm = _llm("no deberia")

        answer = await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        assert answer is not None
        assert answer.content == DEFAULT_UNSUPPORTED_FALLBACK
        assert llm.chat_calls == []

    async def test_the_tenant_can_write_their_own(self, db: Session) -> None:
        """A bakery asks for it in writing; a clinic hands over to a person."""
        tenant = _tenant(db)
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.unsupported_fallback = "Te llamo en un momento."
        db.flush()
        conversation = _thread(db, tenant)
        _said(db, conversation, "[audio]", msg_type="unsupported")

        answer = await ConversationResponder(db, tenant.id, llm=_llm("x")).respond(conversation.id)

        assert answer is not None
        assert answer.content == "Te llamo en un momento."

    async def test_a_photo_with_a_caption_is_still_a_question(self, db: Session) -> None:
        """Mixed bursts are the common case: a picture of the product and
        "cuanto cuesta?". The words are the question."""
        tenant = _tenant(db)
        conversation = _thread(db, tenant)
        _said(db, conversation, "[imagen]", msg_type="image")
        _said(db, conversation, "cuanto cuesta este?")
        llm = _llm("Ese cuesta 12 soles.")

        answer = await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        assert answer is not None
        assert answer.content == "Ese cuesta 12 soles."
        # The placeholder never reaches the model.
        assert "[imagen]" not in llm.chat_calls[-1].messages[-1].content


class TestTheDebounce:
    async def test_the_first_message_books_the_reply(self, db: Session) -> None:
        store = FakeStore()
        queue = RecordingQueue()
        tenant_id, conversation_id = uuid.uuid4(), uuid.uuid4()

        booked = await schedule_answer(
            queue,
            store,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            debounce_seconds=8,
        )

        assert booked is True
        assert queue.enqueued == [
            (ANSWER_JOB, (str(tenant_id), str(conversation_id)), 8),
        ]

    async def test_the_rest_of_the_burst_books_nothing(self, db: Session) -> None:
        """Five messages, one job. Without this the debounce would only move
        the problem into the worker."""
        store = FakeStore()
        queue = RecordingQueue()
        conversation_id = uuid.uuid4()

        for _ in range(5):
            await schedule_answer(
                queue,
                store,
                tenant_id=uuid.uuid4(),
                conversation_id=conversation_id,
                debounce_seconds=8,
            )

        assert len(queue.enqueued) == 1

    async def test_a_later_burst_books_again(self, db: Session) -> None:
        """The failure this guards against is the worst one available: a
        marker that is never cleared means the customer's next question is
        never answered."""
        store = FakeStore()
        queue = RecordingQueue()
        conversation_id = uuid.uuid4()
        await schedule_answer(
            queue,
            store,
            tenant_id=uuid.uuid4(),
            conversation_id=conversation_id,
            debounce_seconds=8,
        )
        # What the answering job does before it reads the transcript.
        await store.delete(debounce_key(conversation_id))

        await schedule_answer(
            queue,
            store,
            tenant_id=uuid.uuid4(),
            conversation_id=conversation_id,
            debounce_seconds=8,
        )

        assert len(queue.enqueued) == 2

    async def test_two_customers_are_not_debounced_together(self, db: Session) -> None:
        store = FakeStore()
        queue = RecordingQueue()

        for _ in range(2):
            await schedule_answer(
                queue,
                store,
                tenant_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                debounce_seconds=8,
            )

        assert len(queue.enqueued) == 2


@pytest.mark.parametrize("size", [1, 2, 10])
async def test_a_burst_of_any_size_is_one_reply(db: Session, size: int, **_: Any) -> None:
    tenant = _tenant(db)
    conversation = _thread(db, tenant)
    for index in range(size):
        _said(db, conversation, f"mensaje {index}")

    await ConversationResponder(db, tenant.id, llm=_llm("una sola respuesta")).respond(
        conversation.id
    )

    outbound = (
        db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id, Message.direction == "outbound"
            )
        )
        .scalars()
        .all()
    )
    assert len(outbound) == 1
