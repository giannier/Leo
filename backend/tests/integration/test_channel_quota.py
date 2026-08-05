"""The meter, live (TASK-207, HU-04).

The trial's limit is 250 replies, and the number that matters is the one the
customer can see while their agent is working. Three things are pinned here:

* the counter is **exact** under concurrency - the feature's own verification
  asks for fifty simultaneous replies and a counter that reads fifty;
* the warning arrives at 80%, once, before the agent goes quiet rather than
  after;
* running out mid-conversation says goodbye and stops *there*, not at the top
  of the next hour.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.plan import Plan
from app.models.tenant import QUOTA_80, Tenant, TenantNotification, TenantUser
from app.models.user import User
from app.providers.llm import ChatResult, MockLLMProvider
from app.services.channel import ConversationResponder
from app.services.trial import record_ai_message

CUSTOMER = "+51987654321"


def _tenant(db: Session, *, used: int = 0, owner_email: str | None = None) -> Tenant:
    tenant = Tenant(
        name=f"Panaderia {uuid.uuid4().hex[:8]}",
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
        ai_messages_used=used,
    )
    db.add(tenant)
    db.flush()
    db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
    if owner_email:
        user = User(
            email=owner_email,
            full_name="Ana Quispe",
            password_hash="x",
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role="owner", status="active"))
    db.flush()
    return tenant


def _thread_with_a_question(db: Session, tenant: Tenant) -> Conversation:
    contact = Contact(tenant_id=tenant.id, phone_e164=CUSTOMER, last_inbound_at=datetime.now(UTC))
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(conversation)
    db.flush()
    db.add(
        Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            content="tienen pan?",
            msg_type="text",
        )
    )
    db.flush()
    return conversation


def _llm(reply: str = "Si, tenemos.") -> MockLLMProvider:
    provider = MockLLMProvider()
    provider.queue(ChatResult(content=reply))
    return provider


def _limit(db: Session) -> int:
    plan = db.get(Plan, "trial")
    assert plan is not None
    return plan.max_ai_messages


class TestTheWarningAtEightyPercent:
    async def test_it_is_sent_when_the_counter_crosses(self, db: Session) -> None:
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        limit = _limit(db)
        tenant = _tenant(db, used=int(limit * 0.8) - 1, owner_email=email)
        conversation = _thread_with_a_question(db, tenant)
        responder = ConversationResponder(db, tenant.id, llm=_llm())

        await responder.respond(conversation.id)

        assert len(responder.pending_emails) == 1
        assert responder.pending_emails[0].to == (email,)

    async def test_it_says_how_many_are_left(self, db: Session) -> None:
        """The useful information is the remainder, not the percentage: it is
        what decides whether they pay before or after the agent stops."""
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        limit = _limit(db)
        tenant = _tenant(db, used=int(limit * 0.8) - 1, owner_email=email)
        conversation = _thread_with_a_question(db, tenant)
        responder = ConversationResponder(db, tenant.id, llm=_llm())

        await responder.respond(conversation.id)

        body = responder.pending_emails[0].text
        assert str(limit - int(limit * 0.8)) in body

    async def test_it_is_not_sent_before_the_threshold(self, db: Session) -> None:
        tenant = _tenant(db, used=10, owner_email=f"ana-{uuid.uuid4().hex[:10]}@example.com")
        conversation = _thread_with_a_question(db, tenant)
        responder = ConversationResponder(db, tenant.id, llm=_llm())

        await responder.respond(conversation.id)

        assert responder.pending_emails == []

    async def test_it_is_sent_only_once(self, db: Session) -> None:
        """Every reply past 80% is past 80%. Without the claim, a busy
        afternoon is fifty identical emails."""
        email = f"ana-{uuid.uuid4().hex[:10]}@example.com"
        limit = _limit(db)
        tenant = _tenant(db, used=int(limit * 0.8) - 1, owner_email=email)
        conversation = _thread_with_a_question(db, tenant)
        await ConversationResponder(db, tenant.id, llm=_llm()).respond(conversation.id)

        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content="y integral?",
                msg_type="text",
            )
        )
        db.flush()
        second = ConversationResponder(db, tenant.id, llm=_llm("Tambien."))
        await second.respond(conversation.id)

        assert second.pending_emails == []
        claims = (
            db.execute(
                select(TenantNotification).where(
                    TenantNotification.tenant_id == tenant.id,
                    TenantNotification.kind == QUOTA_80,
                )
            )
            .scalars()
            .all()
        )
        assert len(claims) == 1


class TestRunningOutMidConversation:
    async def test_the_tenant_is_paused_on_the_message_that_spends_the_last_one(
        self, db: Session
    ) -> None:
        """Clarification 004. The hourly enforcer would get here eventually,
        and "eventually" is up to an hour of free replies from an agent that
        is, on paper, out of messages."""
        limit = _limit(db)
        tenant = _tenant(db, used=limit - 1)
        conversation = _thread_with_a_question(db, tenant)

        await ConversationResponder(db, tenant.id, llm=_llm()).respond(conversation.id)

        db.refresh(tenant)
        assert tenant.status == "paused"
        assert tenant.paused_at is not None

    async def test_the_customer_gets_the_answer_and_then_the_farewell(self, db: Session) -> None:
        """Leaving somebody mid-conversation with silence is the worst version
        of this: they keep typing to a business that has stopped answering and
        no longer says so."""
        limit = _limit(db)
        tenant = _tenant(db, used=limit - 1)
        conversation = _thread_with_a_question(db, tenant)

        await ConversationResponder(db, tenant.id, llm=_llm("Si, tenemos.")).respond(
            conversation.id
        )

        outbound = (
            db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "outbound",
                )
                .order_by(Message.created_at)
            )
            .scalars()
            .all()
        )
        assert [message.author for message in outbound] == ["agent", "system"]
        assert outbound[0].content == "Si, tenemos."

    async def test_the_farewell_is_queued_for_sending(self, db: Session) -> None:
        """Before the real channel existed the row *was* the farewell. Now it
        is only the intention to send one, and a stateless row is a goodbye
        the customer never receives."""
        limit = _limit(db)
        tenant = _tenant(db, used=limit - 1)
        conversation = _thread_with_a_question(db, tenant)

        await ConversationResponder(db, tenant.id, llm=_llm()).respond(conversation.id)

        farewell = (
            db.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id, Message.author == "system"
                )
            )
            .scalars()
            .one()
        )
        assert farewell.delivery_status == "queued"

    async def test_the_thread_is_closed(self, db: Session) -> None:
        limit = _limit(db)
        tenant = _tenant(db, used=limit - 1)
        conversation = _thread_with_a_question(db, tenant)

        await ConversationResponder(db, tenant.id, llm=_llm()).respond(conversation.id)

        db.refresh(conversation)
        assert conversation.status == "closed"

    async def test_the_next_message_is_stored_but_not_answered(self, db: Session) -> None:
        """ "Los mensajes entrantes se siguen recibiendo y almacenando"."""
        limit = _limit(db)
        tenant = _tenant(db, used=limit - 1)
        conversation = _thread_with_a_question(db, tenant)
        await ConversationResponder(db, tenant.id, llm=_llm()).respond(conversation.id)

        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content="hola?",
                msg_type="text",
            )
        )
        db.flush()
        llm = _llm("no deberia")

        answer = await ConversationResponder(db, tenant.id, llm=llm).respond(conversation.id)

        assert answer is None
        assert llm.chat_calls == []


class TestTheCounterUnderConcurrency:
    """The feature's own verification: fifty simultaneous replies, a counter
    that reads fifty.

    Real connections, not the shared rolled-back session the rest of the suite
    uses - the whole point is what two transactions do to each other, and a
    single session cannot contend with itself. The rows are cleaned up by
    hand for the same reason.
    """

    @pytest.fixture
    def own_engine(self, engine: Engine) -> Engine:
        return create_engine(get_settings().database_url, pool_size=20, max_overflow=20)

    def test_fifty_concurrent_replies_are_counted_exactly(self, own_engine: Engine) -> None:
        with Session(own_engine) as setup:
            tenant = Tenant(
                name=f"Concurrencia {uuid.uuid4().hex[:8]}",
                status="trial",
                plan_id="trial",
                trial_ends_at=datetime.now(UTC) + timedelta(days=7),
            )
            setup.add(tenant)
            setup.commit()
            tenant_id = tenant.id

        def meter_one() -> None:
            with Session(own_engine) as session:
                record_ai_message(session, tenant_id, channel="whatsapp")
                session.commit()

        async def run_them_all() -> None:
            await asyncio.gather(
                *(asyncio.to_thread(meter_one) for _ in range(50)),
            )

        try:
            asyncio.run(run_them_all())

            with Session(own_engine) as check:
                counted = check.get(Tenant, tenant_id)
                assert counted is not None
                # Not 49, and not "about 50". Two transactions reading 249 and
                # both writing 250 is one free message to the customer and, far
                # worse, a count that never passes the limit the enforcer
                # watches for.
                assert counted.ai_messages_used == 50
        finally:
            with Session(own_engine) as cleanup:
                doomed = cleanup.get(Tenant, tenant_id)
                if doomed is not None:
                    cleanup.delete(doomed)
                    cleanup.commit()
            own_engine.dispose()
