"""Notices to the business (Feature 004, TASK-431).

The verification of the task: each event creates its notice and its email, the model
being down emails at most once an hour, the customer being unreachable emails nobody,
and the one-shot notices of the trial still refuse a second row.
"""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.orders import Order, PaymentProof
from app.models.tenant import (
    CASH_ORDER,
    CUSTOMER_UNREACHED,
    HANDOFF,
    LLM_DOWN,
    NEW_RECEIPT,
    QUOTA_80,
    Tenant,
    TenantUser,
)
from app.models.user import User
from app.models.whatsapp import WhatsAppNumber
from app.orders.service import OrderService
from app.providers.email.mock import MockEmailProvider
from app.providers.whatsapp.base import SendResult
from app.repositories.notification import TenantNotificationRepository
from app.repositories.orders import PaymentProofRepository
from app.services.activation import owner_of
from app.services.channel import ConversationResponder
from app.services.emails import (
    CASH_ORDER_SUBJECT,
    HANDOFF_SUBJECT,
    LLM_DOWN_SUBJECT,
    NEW_RECEIPT_SUBJECT,
)
from app.services.tenant_alerts import LLM_DOWN_EMAIL_INTERVAL, TenantAlerts, send_emails
from app.services.trial import claim_notification
from app.workers.channel import answer_conversation
from tests.integration.test_proof_pipeline import World, receipt, vision
from tests.unit.test_locks import FakeStore

D = Decimal
# The database stamps `created_at` with its own clock, so the tests' clock starts at the
# real one: the rate limit compares the two.
NOW = datetime.now(UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name="Tienda <Demo>")
    db.add(row)
    db.flush()
    owner = User(
        email=f"dueno-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", full_name="Dueño"
    )
    db.add(owner)
    db.flush()
    db.add(TenantUser(tenant_id=row.id, user_id=owner.id, role="owner", status="active"))
    db.flush()
    return row


def owner_email(db: Session, tenant: Tenant) -> str:
    owner = owner_of(db, tenant.id)
    assert owner is not None
    return str(owner.email)


def make_order(
    db: Session, tenant: Tenant, *, sandbox: bool = False
) -> tuple[Order, PaymentProof, Conversation]:
    contact = Contact(tenant_id=tenant.id, phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}")
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="sandbox" if sandbox else "whatsapp", contact_id=contact.id
    )
    db.add(conversation)
    db.flush()
    order = OrderService(db, tenant.id).open_draft(
        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=sandbox
    )
    order.total = D("120.00")
    proof = PaymentProofRepository(db, tenant.id).add(
        order_id=order.id,
        conversation_id=conversation.id,
        storage_key="k.jpg",
        observaciones=["monto_distinto"],
    )
    return order, proof, conversation


def notices(db: Session, tenant: Tenant, kind: str) -> list[dict[str, object]]:
    return [n.payload for n in TenantNotificationRepository(db, tenant.id).recent(kind)]


class TestEachEventLeavesANoticeAndItsEmail:
    def test_a_new_receipt(self, db: Session, tenant: Tenant) -> None:
        order, proof, _ = make_order(db, tenant)
        alerts = TenantAlerts(db, tenant.id)

        alerts.new_proof(order, proof)

        [payload] = notices(db, tenant, NEW_RECEIPT)
        assert payload["order_id"] == str(order.id) and payload["numero"] == order.numero
        assert payload["proof_id"] == str(proof.id)
        assert payload["observaciones"] == ["monto_distinto"]
        [email] = alerts.pending_emails
        assert email.to == (owner_email(db, tenant),)
        assert email.subject == NEW_RECEIPT_SUBJECT
        assert f"#{order.numero}" in email.html and "S/ 120.00" in email.html
        # The business name is escaped in the HTML body and plain in the text one.
        assert "Tienda &lt;Demo&gt;" in email.html
        assert email.text is not None and "Tienda <Demo>" in email.text

    def test_a_cash_on_delivery_order(self, db: Session, tenant: Tenant) -> None:
        order, _, _ = make_order(db, tenant)
        alerts = TenantAlerts(db, tenant.id)

        alerts.cash_order(order)

        [payload] = notices(db, tenant, CASH_ORDER)
        assert payload["order_id"] == str(order.id)
        [email] = alerts.pending_emails
        assert email.subject == CASH_ORDER_SUBJECT
        assert "contra entrega" in email.html

    def test_a_handoff_carries_its_summary(self, db: Session, tenant: Tenant) -> None:
        conversation_id = uuid.uuid4()
        alerts = TenantAlerts(db, tenant.id)

        alerts.handoff(conversation_id, motivo="pide_humano", resumen="Quiere <b>factura</b>")

        [payload] = notices(db, tenant, HANDOFF)
        assert payload == {
            "conversation_id": str(conversation_id),
            "motivo": "pide_humano",
            "resumen": "Quiere <b>factura</b>",
        }
        [email] = alerts.pending_emails
        assert email.subject == HANDOFF_SUBJECT
        assert "Quiere &lt;b&gt;factura&lt;/b&gt;" in email.html
        assert email.text is not None and "Quiere <b>factura</b>" in email.text

    def test_a_handoff_without_summary_still_notifies(self, db: Session, tenant: Tenant) -> None:
        alerts = TenantAlerts(db, tenant.id)

        alerts.handoff(uuid.uuid4(), motivo="queja")

        assert len(alerts.pending_emails) == 1
        assert "Resumen" not in alerts.pending_emails[0].html

    def test_the_model_being_down(self, db: Session, tenant: Tenant) -> None:
        alerts = TenantAlerts(db, tenant.id, clock=Clock())

        alerts.llm_down()

        assert notices(db, tenant, LLM_DOWN) == [{"correo": True}]
        [email] = alerts.pending_emails
        assert email.subject == LLM_DOWN_SUBJECT

    @pytest.mark.parametrize("reason", ["ventana_cerrada", "envio_fallido"])
    def test_the_customer_not_being_told_leaves_a_notice_and_no_email(
        self, db: Session, tenant: Tenant, reason: str
    ) -> None:
        order, _, conversation = make_order(db, tenant)
        alerts = TenantAlerts(db, tenant.id)

        alerts.customer_not_notified(order, reason)  # type: ignore[arg-type]

        [payload] = notices(db, tenant, CUSTOMER_UNREACHED)
        assert payload["motivo"] == reason
        assert payload["conversation_id"] == str(conversation.id)
        assert alerts.pending_emails == []


class TestTheSimulatorNotifiesNobody:
    def test_no_notice_and_no_email_for_a_simulated_order(
        self, db: Session, tenant: Tenant
    ) -> None:
        order, proof, _ = make_order(db, tenant, sandbox=True)
        alerts = TenantAlerts(db, tenant.id)

        alerts.new_proof(order, proof)
        alerts.cash_order(order)
        alerts.customer_not_notified(order, "ventana_cerrada")

        assert TenantNotificationRepository(db, tenant.id).recent() == []
        assert alerts.pending_emails == []


class TestTheModelDownEmailIsLimited:
    def test_one_email_per_hour_but_every_opening_leaves_its_notice(
        self, db: Session, tenant: Tenant
    ) -> None:
        clock = Clock()
        alerts = TenantAlerts(db, tenant.id, clock=clock)

        alerts.llm_down()
        clock.now += timedelta(minutes=5)
        alerts.llm_down()
        clock.now += timedelta(minutes=50)
        alerts.llm_down()

        assert len(alerts.pending_emails) == 1
        # One row per opening (they share a transaction here, so compare as a set).
        flags = sorted((bool(n["correo"]) for n in notices(db, tenant, LLM_DOWN)), reverse=True)
        assert flags == [True, False, False]

    def test_after_the_hour_it_emails_again(self, db: Session, tenant: Tenant) -> None:
        clock = Clock()
        alerts = TenantAlerts(db, tenant.id, clock=clock)
        alerts.llm_down()
        # `created_at` is the database's own clock, so age the row rather than the test's.
        for row in TenantNotificationRepository(db, tenant.id).recent(LLM_DOWN):
            row.created_at = NOW - LLM_DOWN_EMAIL_INTERVAL - timedelta(minutes=1)
        db.flush()

        alerts.llm_down()

        assert len(alerts.pending_emails) == 2

    def test_another_tenants_email_does_not_count(self, db: Session, tenant: Tenant) -> None:
        other = Tenant(name="Otra")
        db.add(other)
        db.flush()
        TenantAlerts(db, other.id, clock=Clock()).llm_down()
        mine = TenantAlerts(db, tenant.id, clock=Clock())

        mine.llm_down()

        assert len(mine.pending_emails) == 1


class TestSending:
    async def test_the_emails_go_out_through_the_provider(
        self, db: Session, tenant: Tenant
    ) -> None:
        order, proof, _ = make_order(db, tenant)
        alerts = TenantAlerts(db, tenant.id)
        alerts.new_proof(order, proof)
        mail = MockEmailProvider()

        await send_emails(mail, alerts.pending_emails)

        assert [m.subject for m in mail.sent] == [NEW_RECEIPT_SUBJECT]

    async def test_a_provider_that_fails_or_is_missing_never_raises(
        self, db: Session, tenant: Tenant
    ) -> None:
        class Broken:
            async def send(self, message: object) -> str:
                raise RuntimeError("mail is down")

        alerts = TenantAlerts(db, tenant.id)
        alerts.handoff(uuid.uuid4(), motivo="queja")

        await send_emails(Broken(), alerts.pending_emails)  # type: ignore[arg-type]
        await send_emails(None, alerts.pending_emails)
        await send_emails(MockEmailProvider(), [])

    def test_a_business_without_an_owner_still_gets_its_notice(self, db: Session) -> None:
        orphan = Tenant(name="Sin dueño")
        db.add(orphan)
        db.flush()
        alerts = TenantAlerts(db, orphan.id)

        alerts.handoff(uuid.uuid4(), motivo="queja")

        assert len(notices(db, orphan, HANDOFF)) == 1
        assert alerts.pending_emails == []


class TestOneShotNoticesStillRefuseASecondRow:
    def test_a_one_shot_kind_is_claimed_once(self, db: Session, tenant: Tenant) -> None:
        assert claim_notification(db, tenant.id, QUOTA_80) is True
        assert claim_notification(db, tenant.id, QUOTA_80) is False

    def test_the_database_itself_refuses_the_duplicate(self, db: Session, tenant: Tenant) -> None:
        repository = TenantNotificationRepository(db, tenant.id)
        repository.record(QUOTA_80)

        with pytest.raises(IntegrityError):
            repository.record(QUOTA_80)


class TestWiring:
    async def test_a_receipt_on_the_real_channel_notifies_after_the_turn(self, db: Session) -> None:
        """Through the responder, as the worker builds it: the notice is written in the
        turn's transaction and the email waits to be sent after the commit."""
        world = World(db, sandbox=False)
        owner = User(
            email=f"dueno-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", full_name="D"
        )
        db.add(owner)
        db.flush()
        db.add(
            TenantUser(tenant_id=world.tenant.id, user_id=owner.id, role="owner", status="active")
        )
        db.flush()
        photo = Message(
            tenant_id=world.tenant.id,
            conversation_id=world.conversation.id,
            direction="inbound",
            author="customer",
            content="[imagen]",
            msg_type="image",
            provider_message_id="wamid.photo",
            payload={"image": {"storage_key": "k.jpg", "mime_type": "image/jpeg"}},
        )
        db.add(photo)
        db.flush()
        world.storage.put("k.jpg", b"jpeg", content_type="image/jpeg")
        alerts = TenantAlerts(db, world.tenant.id)
        responder = ConversationResponder(
            db, world.tenant.id, llm=vision(receipt()), storage=world.storage, notices=alerts
        )

        await responder.respond_turn(world.conversation.id)

        [notice] = TenantNotificationRepository(db, world.tenant.id).recent(NEW_RECEIPT)
        [proof] = world.stored_proofs()
        assert notice.payload["proof_id"] == str(proof.id)
        assert [m.subject for m in alerts.pending_emails] == [NEW_RECEIPT_SUBJECT]


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, credentials: object, *, sender: str, to: object, body: str) -> object:
        self.sent.append(body)
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")


class TestTheWorkerSendsTheEmailsAfterTheCommit:
    async def test_a_receipt_in_a_real_turn_leaves_its_notice_and_mails_the_owner(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`answer_conversation`, end to end with every provider replaced: the notice is
        in the database and the owner's email was sent once the turn was committed."""
        world = World(db, sandbox=False)
        tenant = world.tenant
        tenant.status = "trial"
        tenant.trial_ends_at = datetime.now(UTC) + timedelta(days=7)
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.is_active = True
        owner = User(
            email=f"dueno-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", full_name="D"
        )
        db.add(owner)
        db.flush()
        db.add(TenantUser(tenant_id=tenant.id, user_id=owner.id, role="owner", status="active"))
        db.add(
            WhatsAppNumber(
                tenant_id=tenant.id,
                phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
                display_name="Tienda",
                provider="ycloud",
                status="connected",
                credentials_enc=encrypt_secret(
                    json.dumps({"waba_id": "1", "phone_number_id": "2", "api_key": "yc_key_1"})
                ),
            )
        )
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=world.conversation.id,
                direction="inbound",
                author="customer",
                content="[imagen]",
                msg_type="image",
                provider_message_id="wamid.photo",
                payload={"image": {"storage_key": "k.jpg", "mime_type": "image/jpeg"}},
            )
        )
        db.flush()
        world.storage.put("k.jpg", b"jpeg", content_type="image/jpeg")
        llm = vision(receipt())
        monkeypatch.setattr(
            "app.workers.channel.chat_provider_for", lambda *_args: (llm, "economico")
        )
        mail = MockEmailProvider()
        whatsapp = FakeWhatsApp()
        ctx = {
            "redis": FakeStore(),
            "settings": get_settings(),
            "session_factory": lambda: nullcontext(db),
            "whatsapp": whatsapp,
            "email": mail,
            "storage": world.storage,
        }

        outcome = await answer_conversation(ctx, str(tenant.id), str(world.conversation.id))

        assert outcome == "answered"
        assert whatsapp.sent == ["Tu comprobante está en verificación."]
        [notice] = TenantNotificationRepository(db, tenant.id).recent(NEW_RECEIPT)
        assert notice.payload["numero"] == world.order.numero
        assert [m.subject for m in mail.sent] == [NEW_RECEIPT_SUBJECT]
        assert mail.sent[0].to == (owner.email,)
