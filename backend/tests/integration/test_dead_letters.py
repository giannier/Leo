"""The dead-letter queue and the channel metrics (TASK-208).

`dead_letter` is only "not a discard" if somebody can see it. This is that
somebody: every row here is a customer's message that never got an answer, and
the queue exists so that a parser bug found on Tuesday can be fixed and the
Monday messages replayed rather than mourned.

The metrics are the other half of RF-008. Latency is measured from the
customer's message to the reply that answered it - what they actually waited -
rather than from the job's start, which would report how fast the model was
and hide the eight seconds of debounce and any time spent in a queue.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.channel import WebhookEventRepository
from app.services.admin import PlatformAdminService
from tests.fixtures import load
from tests.integration.test_platform_admin import _admin as _signed_in_admin
from tests.integration.test_platform_admin import _register
from tests.integration.test_webhook_endpoint import RecordingQueue

ADMIN = "/api/v1/platform-admin"


def _admin(db: Session) -> User:
    user = User(
        email=f"admin-{uuid.uuid4().hex[:10]}@leo.pe",
        full_name="Operador",
        password_hash="x",
        email_verified_at=datetime.now(UTC),
        is_platform_admin=True,
    )
    db.add(user)
    db.flush()
    return user


def _dead_letter(db: Session, payload: dict[str, Any] | None = None) -> uuid.UUID:
    events = WebhookEventRepository(db)
    event = events.record(
        provider="ycloud",
        provider_event_id=f"evt_{uuid.uuid4().hex}",
        payload=payload or load("ycloud/inbound_text.json"),
    )
    assert event is not None
    event.status = "dead_letter"
    event.attempts = 5
    event.error_msg = "UnknownNumberError: no connected number matches"
    db.flush()
    return event.id


class TestTheQueue:
    def test_it_shows_what_the_row_is_about_without_expanding_it(self, db: Session) -> None:
        """An operator scanning fifty rows needs the type and the number on
        the line, not a JSON blob to open one at a time."""
        actor = _admin(db)
        _dead_letter(db)

        letters = PlatformAdminService(db, actor=actor).dead_letters()

        mine = [row for row in letters if row.attempts == 5]
        assert mine
        assert mine[0].event_type == "whatsapp.inbound_message.received"
        assert mine[0].phone_e164 == "+51987654321"

    def test_a_payload_that_is_itself_the_problem_still_lists(self, db: Session) -> None:
        """A malformed payload is one of the reasons an event lands here, so
        reading it defensively is the whole job."""
        actor = _admin(db)
        event_id = _dead_letter(db, {"nothing": "recognisable"})

        letters = PlatformAdminService(db, actor=actor).dead_letters()

        listed = [row for row in letters if row.id == event_id]
        assert listed
        assert listed[0].event_type is None
        assert listed[0].phone_e164 is None

    def test_the_error_that_stopped_it_is_carried(self, db: Session) -> None:
        actor = _admin(db)
        event_id = _dead_letter(db)

        letters = PlatformAdminService(db, actor=actor).dead_letters()

        listed = [row for row in letters if row.id == event_id]
        assert "UnknownNumberError" in (listed[0].error_msg or "")


class TestRetrying:
    def test_it_goes_back_with_a_full_budget(self, db: Session) -> None:
        """Not one last chance: whatever was fixed in between deserves a fair
        run."""
        actor = _admin(db)
        event_id = _dead_letter(db)

        PlatformAdminService(db, actor=actor).retry_dead_letter(event_id)

        event = WebhookEventRepository(db).get(event_id)
        assert event is not None
        assert event.status == "received"
        assert event.attempts == 0
        assert event.error_msg is None

    def test_it_is_audited(self, db: Session) -> None:
        """An operator replaying somebody else's message is exactly the kind
        of cross-tenant action the audit log exists for."""
        from sqlalchemy import select

        from app.models.audit import AuditLog

        actor = _admin(db)
        event_id = _dead_letter(db)

        PlatformAdminService(db, actor=actor).retry_dead_letter(event_id)

        entries = (
            db.execute(
                select(AuditLog).where(
                    AuditLog.actor_id == actor.id, AuditLog.action == "webhook.retried"
                )
            )
            .scalars()
            .all()
        )
        assert len(entries) == 1
        assert entries[0].detail["event_id"] == str(event_id)

    def test_an_event_that_is_not_dead_cannot_be_retried(self, db: Session) -> None:
        """Replaying a `processed` event would apply a customer's message a
        second time and answer them twice."""
        from app.services.admin import DeadLetterNotFound

        actor = _admin(db)
        event_id = _dead_letter(db)
        event = WebhookEventRepository(db).get(event_id)
        assert event is not None
        event.status = "processed"
        db.flush()

        with pytest.raises(DeadLetterNotFound):
            PlatformAdminService(db, actor=actor).retry_dead_letter(event_id)

    async def test_the_endpoint_hands_it_to_a_worker(
        self, api: AsyncClient, app: FastAPI, db: Session, mail: Any
    ) -> None:
        """Enqueued *after* the service commits: a job queued from inside a
        transaction that then rolls back is a worker reading a row that does
        not exist."""
        recording = RecordingQueue()
        app.dependency_overrides[get_job_queue] = lambda: recording
        headers, _ = await _signed_in_admin(api, mail, db)
        event_id = _dead_letter(db)

        response = await api.post(f"{ADMIN}/dead-letters/{event_id}/retry", headers=headers)

        assert response.status_code == 200, response.text
        assert recording.enqueued == [("process_webhook_event", (str(event_id),))]

    async def test_the_queue_is_readable_over_http(
        self, api: AsyncClient, db: Session, mail: Any
    ) -> None:
        headers, _ = await _signed_in_admin(api, mail, db)
        event_id = _dead_letter(db)

        listed = await api.get(f"{ADMIN}/dead-letters", headers=headers)

        assert listed.status_code == 200, listed.text
        assert str(event_id) in {row["id"] for row in listed.json()}

    async def test_it_is_closed_to_everybody_else(
        self, api: AsyncClient, db: Session, mail: Any
    ) -> None:
        """404 rather than 403: the existence of this queue is not something
        an ordinary tenant needs confirmed."""
        headers, _ = await _register(api, mail, db)
        event_id = _dead_letter(db)

        refused = await api.post(f"{ADMIN}/dead-letters/{event_id}/retry", headers=headers)

        assert refused.status_code == 404


class TestChannelMetrics:
    def _conversation(self, db: Session, tenant: Tenant) -> Conversation:
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

    def _exchange(
        self,
        db: Session,
        conversation: Conversation,
        *,
        asked_at: datetime,
        answered_after_ms: int,
    ) -> None:
        db.add(
            Message(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content="tienen pan?",
                msg_type="text",
                created_at=asked_at,
            )
        )
        db.add(
            Message(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="Si.",
                msg_type="text",
                delivery_status="sent",
                created_at=asked_at + timedelta(milliseconds=answered_after_ms),
            )
        )
        db.flush()

    def _tenant(self, db: Session) -> Tenant:
        tenant = Tenant(name=f"Metricas {uuid.uuid4().hex[:8]}")
        db.add(tenant)
        db.flush()
        return tenant

    def test_traffic_is_counted_both_ways(self, db: Session) -> None:
        actor = _admin(db)
        tenant = self._tenant(db)
        conversation = self._conversation(db, tenant)
        now = datetime.now(UTC)
        self._exchange(db, conversation, asked_at=now, answered_after_ms=1000)

        metrics = PlatformAdminService(db, actor=actor).channel_metrics(
            tenant.id, since=now - timedelta(days=1)
        )

        assert (metrics.messages_in, metrics.messages_out) == (1, 1)

    def test_latency_is_what_the_customer_waited(self, db: Session) -> None:
        actor = _admin(db)
        tenant = self._tenant(db)
        now = datetime.now(UTC)
        for delay in (1000, 2000, 3000):
            self._exchange(
                db, self._conversation(db, tenant), asked_at=now, answered_after_ms=delay
            )

        metrics = PlatformAdminService(db, actor=actor).channel_metrics(
            tenant.id, since=now - timedelta(days=1)
        )

        assert metrics.p50_response_ms == 2000
        assert metrics.p95_response_ms == 3000

    def test_a_burst_is_timed_from_the_first_message(self, db: Session) -> None:
        """Somebody who sends four lines waited from the first one. Timing
        from the last would report the debounce as instant service."""
        actor = _admin(db)
        tenant = self._tenant(db)
        conversation = self._conversation(db, tenant)
        now = datetime.now(UTC)
        for offset in (0, 2000, 4000):
            db.add(
                Message(
                    tenant_id=tenant.id,
                    conversation_id=conversation.id,
                    direction="inbound",
                    author="customer",
                    content="hola",
                    msg_type="text",
                    created_at=now + timedelta(milliseconds=offset),
                )
            )
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="Buenas!",
                msg_type="text",
                created_at=now + timedelta(milliseconds=9000),
            )
        )
        db.flush()

        metrics = PlatformAdminService(db, actor=actor).channel_metrics(
            tenant.id, since=now - timedelta(days=1)
        )

        assert metrics.p50_response_ms == 9000

    def test_failed_sends_are_counted(self, db: Session) -> None:
        actor = _admin(db)
        tenant = self._tenant(db)
        conversation = self._conversation(db, tenant)
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="no llego",
                msg_type="text",
                delivery_status="failed",
            )
        )
        db.flush()

        metrics = PlatformAdminService(db, actor=actor).channel_metrics(
            tenant.id, since=datetime.now(UTC) - timedelta(days=1)
        )

        assert metrics.failed_sends == 1

    def test_a_silent_period_reports_nothing_rather_than_zero(self, db: Session) -> None:
        """A dashboard showing 0 ms on a day nobody wrote in reads as very
        fast, which is the opposite of what happened."""
        actor = _admin(db)
        tenant = self._tenant(db)

        metrics = PlatformAdminService(db, actor=actor).channel_metrics(
            tenant.id, since=datetime.now(UTC) - timedelta(days=1)
        )

        assert metrics.p50_response_ms is None
        assert metrics.p95_response_ms is None

    def test_the_sandbox_is_not_counted_as_real_traffic(self, db: Session) -> None:
        """Otherwise an owner is told their number was busy on a day nobody
        wrote to them."""
        actor = _admin(db)
        tenant = self._tenant(db)
        sandbox = Conversation(tenant_id=tenant.id, channel="sandbox", status="open")
        db.add(sandbox)
        db.flush()
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=sandbox.id,
                direction="inbound",
                author="customer",
                content="probando",
                msg_type="text",
            )
        )
        db.flush()

        metrics = PlatformAdminService(db, actor=actor).channel_metrics(
            tenant.id, since=datetime.now(UTC) - timedelta(days=1)
        )

        assert metrics.messages_in == 0
