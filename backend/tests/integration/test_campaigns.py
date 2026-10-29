"""Campaigns: who they reach, when they go, what stops them, what they achieved (011)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaigns import Campaign, CampaignRecipient
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.crm import Lead, PipelineStage
from app.models.orders import OrderItem
from app.models.tenant import Tenant
from app.providers.whatsapp.base import QualityUpdate, StatusUpdate
from app.providers.whatsapp.ycloud import YCloudProvider
from app.repositories.channel import WebhookEventRepository
from app.repositories.crm import PipelineStageRepository
from app.repositories.orders import OrderRepository
from app.services.campaigns import CampaignError
from app.services.campaigns.report import campaign_report
from app.services.campaigns.segments import Segment, count
from app.services.campaigns.service import CampaignRunner, CampaignService
from app.services.channel import ChannelEventProcessor
from app.workers.channel import process_event
from tests.fixtures import load
from tests.integration.campaign_fixtures import FakeWhatsApp, business, customer, template

# 15:00 UTC is 10:00 in Lima: inside the default 09:00-20:00 window.
NOON = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
NIGHT = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)  # 22:00 in Lima


def _campaign(
    db: Session, tenant: Tenant, *, segment: dict[str, Any] | None = None, **template_kw: Any
) -> Campaign:
    used = template(db, tenant, **template_kw)
    return CampaignService(db, tenant.id).create(
        {"name": "Promo julio", "template_id": used.id, "segment": segment or {}}, by=None
    )


def _recipients(db: Session, campaign: Campaign) -> dict[uuid.UUID, CampaignRecipient]:
    return {
        r.contact_id: r
        for r in db.execute(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
        ).scalars()
    }


async def _run(db: Session, fake: FakeWhatsApp, now: datetime = NOON) -> int:
    return await CampaignRunner(db, provider=fake, storage=None, now=now).tick()


def _lead(
    db: Session, tenant: Tenant, contact: Contact, semantic: str, product: str | None = None
) -> None:
    PipelineStageRepository(db, tenant.id).ensure_defaults()
    stage = db.execute(
        select(PipelineStage).where(
            PipelineStage.tenant_id == tenant.id, PipelineStage.semantic == semantic
        )
    ).scalar_one()
    db.add(
        Lead(
            tenant_id=tenant.id,
            contact_id=contact.id,
            stage_id=stage.id,
            status="active",
            product_interest=product,
        )
    )
    db.flush()


def _bought(db: Session, tenant: Tenant, contact: Contact, item: str, *, at: datetime) -> None:
    thread = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="closed"
    )
    db.add(thread)
    db.flush()
    order = OrderRepository(db, tenant.id).add_numbered(
        conversation_id=thread.id,
        contact_id=contact.id,
        moneda="PEN",
        subtotal=Decimal("80"),
        total=Decimal("80"),
        estado="confirmado",
    )
    order.confirmed_at = at
    db.add(
        OrderItem(
            tenant_id=tenant.id,
            order_id=order.id,
            nombre=item,
            precio_unitario=Decimal("80"),
            cantidad=1,
            subtotal=Decimal("80"),
        )
    )
    db.flush()


class TestSegments:
    def test_permission_and_baja_always_apply(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant, permission="granted")
        customer(db, tenant, permission="declined")
        customer(db, tenant, permission="revoked")
        customer(db, tenant, permission=None)
        customer(db, tenant, permission="granted", opted_out=True)
        assert count(db, tenant.id, Segment(), now=NOON) == 1

    def test_the_filters_combine(self, db: Session) -> None:
        tenant, _ = business(db)
        # The segment is counted at NOON: the customers wrote relative to NOON too, not to today.
        vip = customer(db, tenant, tags=["vip"], now=NOON)
        buyer = customer(db, tenant, tags=["vip"], now=NOON)
        _bought(db, tenant, buyer, "Pan de yema", at=NOON - timedelta(days=3))
        lead = customer(db, tenant, now=NOON)
        _lead(db, tenant, lead, "por_pagar", product="Torta de chocolate")
        old = customer(db, tenant, wrote_hours_ago=24 * 40, now=NOON)

        def ids(**raw: Any) -> int:
            return count(db, tenant.id, Segment.parse(raw), now=NOON)

        assert ids(etiquetas=["vip"]) == 2
        assert ids(etiquetas=["vip"], compro=True) == 1
        assert ids(compro=False) == 3
        assert ids(etapas=["por_pagar"]) == 1
        assert ids(producto="yema") == 1
        assert ids(producto="torta") == 1
        assert ids(dias_sin_escribir_min=30) == 1
        assert ids(dias_sin_escribir_max=7) == 3
        assert {vip.id, old.id}  # all four exist

    @pytest.mark.parametrize(
        "raw",
        [{"etapas": ["inventada"]}, {"dias_sin_escribir_min": 10, "dias_sin_escribir_max": 2}],
    )
    def test_a_bad_filter_is_refused(self, raw: dict[str, Any]) -> None:
        with pytest.raises(CampaignError):
            Segment.parse(raw)


class TestScheduling:
    def test_it_needs_an_approved_template(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant)
        campaign = _campaign(db, tenant, status="pending")
        with pytest.raises(CampaignError) as caught:
            CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        assert caught.value.code == "plantilla_no_aprobada"

    def test_it_needs_somebody_with_permission(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant, permission=None)
        campaign = _campaign(db, tenant)
        with pytest.raises(CampaignError) as caught:
            CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        assert caught.value.code == "sin_destinatarios"

    def test_a_number_in_trouble_does_not_start_one(self, db: Session) -> None:
        tenant, _ = business(db, quality="YELLOW")
        customer(db, tenant)
        campaign = _campaign(db, tenant)
        with pytest.raises(CampaignError) as caught:
            CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        assert caught.value.code == "calidad_baja"

    def test_the_estimate_counts_and_prices(self, db: Session) -> None:
        tenant, _ = business(db)
        for _ in range(3):
            customer(db, tenant)
        campaign = _campaign(db, tenant)
        service = CampaignService(db, tenant.id)
        estimate = service.estimate(Segment(), service.template(campaign.template_id), now=NOON)
        assert estimate["destinatarios"] == 3
        assert estimate["tarifa_usd"] == "0.08085"  # Peru since 2026-10-01 (C-111)
        assert estimate["costo_estimado_usd"] == "0.24"
        service.schedule(campaign, now=NOON)
        assert campaign.status == "scheduled" and campaign.estimated_cost_usd == Decimal("0.2400")

    def test_the_plan_caps_warn_and_with_billing_on_they_stop(self, db: Session) -> None:
        tenant, _ = business(db, plan="trial")  # 1 campaign x 100 people
        customer(db, tenant)
        first = _campaign(db, tenant)
        service = CampaignService(db, tenant.id)
        service.schedule(first, now=NOON)
        second = _campaign(db, tenant, label="Otra")
        # The test suite runs with billing on (conftest), so the cap is applied.
        with pytest.raises(CampaignError) as caught:
            service.schedule(second, now=NOON)
        assert caught.value.code == "limite_plan"

    def test_with_billing_off_the_cap_is_only_a_warning(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "billing_enforced", False)
        tenant, _ = business(db, plan="trial")
        customer(db, tenant)
        service = CampaignService(db, tenant.id)
        service.schedule(_campaign(db, tenant), now=NOON)
        second = _campaign(db, tenant, label="Otra")
        service.schedule(second, now=NOON)
        assert second.status == "scheduled"

    def test_the_window_must_be_daytime(self, db: Session) -> None:
        tenant, _ = business(db)
        used = template(db, tenant)
        with pytest.raises(CampaignError) as caught:
            CampaignService(db, tenant.id).create(
                {"name": "x", "template_id": used.id, "window_from": "06:00", "window_to": "20:00"},
                by=None,
            )
        assert caught.value.code == "horario_invalido"


class TestSending:
    async def test_a_batch_goes_with_each_name_and_skips_who_cannot_get_it(
        self, db: Session
    ) -> None:
        tenant, _ = business(db, name="Panadería Rosa")
        ana = customer(db, tenant, name="Ana")
        nameless = customer(db, tenant, name=None)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        fake = FakeWhatsApp()

        assert await _run(db, fake) == 1

        [sent] = fake.templates
        assert sent["to"] == ana.phone_e164 and sent["values"] == ["Ana", "Panadería Rosa"]
        rows = _recipients(db, campaign)
        assert rows[ana.id].status == "sent" and rows[ana.id].message_id is not None
        assert (
            rows[nameless.id].status == "skipped" and rows[nameless.id].skip_reason == "sin_nombre"
        )
        db.refresh(campaign)
        assert campaign.status == "completed"
        message = db.get(Message, rows[ana.id].message_id)
        assert message is not None and message.msg_type == "template"
        assert message.content == "Hola Ana, en Panadería Rosa tenemos 2x1 esta semana."

    async def test_nothing_goes_outside_the_hours(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NIGHT - timedelta(hours=1))
        fake = FakeWhatsApp()
        assert await _run(db, fake, now=NIGHT) == 0
        assert fake.templates == [] and campaign.status == "sending"
        assert await _run(db, fake, now=NIGHT + timedelta(hours=12)) == 1

    async def test_baja_after_scheduling_is_respected_at_send_time(self, db: Session) -> None:
        tenant, _ = business(db)
        contact = customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        runner = CampaignRunner(db, provider=FakeWhatsApp(), storage=None, now=NIGHT)
        await runner.tick()  # snapshot, outside hours
        contact.opted_out_at = NIGHT
        db.flush()
        fake = FakeWhatsApp()
        await _run(db, fake, now=NIGHT + timedelta(hours=12))
        assert fake.templates == []
        assert _recipients(db, campaign)[contact.id].skip_reason == "baja"

    async def test_a_contact_never_gets_the_same_campaign_twice(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        fake = FakeWhatsApp()
        await _run(db, fake)
        campaign.status = "sending"  # as if a second worker picked it up
        db.flush()
        await _run(db, fake)
        assert len(fake.templates) == 1

    async def test_the_daily_cap_of_the_number(self, db: Session) -> None:
        tenant, number = business(db)
        number.limite_diario = 2
        for _ in range(4):
            customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        fake = FakeWhatsApp()
        assert await _run(db, fake) == 2
        assert await _run(db, fake, now=NOON + timedelta(minutes=1)) == 0
        assert campaign.status == "sending"

    async def test_a_refusal_is_a_failed_recipient_not_a_stop(self, db: Session) -> None:
        tenant, _ = business(db)
        bad = customer(db, tenant)
        good = customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        fake = FakeWhatsApp()
        fake.fail_for = {bad.phone_e164 or ""}
        await _run(db, fake)
        rows = _recipients(db, campaign)
        assert rows[bad.id].status == "failed" and rows[good.id].status == "sent"

    async def test_without_a_number_the_campaign_pauses(self, db: Session) -> None:
        tenant, number = business(db)
        customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        number.status = "disconnected"
        db.flush()
        await _run(db, FakeWhatsApp())
        assert campaign.status == "paused" and campaign.pause_reason == "sin_numero"


class TestQuality:
    async def test_a_yellow_rating_pauses_and_blocks_resuming(self, db: Session) -> None:
        tenant, number = business(db)
        customer(db, tenant)
        campaign = _campaign(db, tenant)
        service = CampaignService(db, tenant.id)
        service.schedule(campaign, now=NOON)

        ChannelEventProcessor(db).apply_quality(
            QualityUpdate(phone_e164=number.phone_e164, quality_rating="YELLOW")
        )
        db.refresh(campaign)
        assert campaign.status == "paused" and campaign.pause_reason == "calidad"
        with pytest.raises(CampaignError) as caught:
            service.resume(campaign)
        assert caught.value.code == "calidad_baja"

        ChannelEventProcessor(db).apply_quality(
            QualityUpdate(phone_e164=number.phone_e164, quality_rating="GREEN")
        )
        service.resume(campaign)
        assert campaign.status == "scheduled"


class TestTheAnswer:
    async def test_a_reply_continues_the_thread_and_counts(self, db: Session) -> None:
        tenant, number = business(db)
        contact = customer(db, tenant, wrote_hours_ago=24 * 10, now=NOON)
        old = Conversation(
            tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
        )
        db.add(old)
        db.flush()
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        await _run(db, FakeWhatsApp())
        row = _recipients(db, campaign)[contact.id]
        assert row.status == "sent"
        db.refresh(old)
        assert old.status == "closed"
        thread = db.get(Conversation, row.conversation_id)
        assert thread is not None and thread.origin_campaign_id == campaign.id

        payload = load("ycloud/inbound_text.json")
        message = payload["whatsappInboundMessage"]
        message.update(id=uuid.uuid4().hex, to=number.phone_e164, **{"from": contact.phone_e164})
        message["text"]["body"] = "¡Sí! ¿Cuánto cuesta?"
        message["sendTime"] = (NOON + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        event = WebhookEventRepository(db).record(
            provider="ycloud", provider_event_id=f"evt_{uuid.uuid4().hex}", payload=payload
        )
        assert event is not None
        outcome = process_event(db, event.id, provider=YCloudProvider())

        assert outcome.conversation_id == thread.id  # the same thread, not a new one
        db.refresh(row)
        assert row.status == "replied" and row.replied_at is not None

    async def test_delivery_reports_move_the_recipient_forward_only(self, db: Session) -> None:
        tenant, _ = business(db)
        contact = customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        await _run(db, FakeWhatsApp())
        row = _recipients(db, campaign)[contact.id]
        message = db.get(Message, row.message_id)
        assert message is not None and message.provider_message_id is not None
        processor = ChannelEventProcessor(db)
        for status in ("read", "delivered"):
            processor.apply_status(
                StatusUpdate(
                    provider_message_id=message.provider_message_id,
                    status=status,
                    occurred_at=NOON,
                )
            )
        db.refresh(row)
        assert row.status == "read"


class TestReport:
    async def test_sales_within_72_hours_are_the_campaigns(self, db: Session) -> None:
        tenant, _ = business(db)
        buyer = customer(db, tenant)
        late = customer(db, tenant)
        quiet = customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        await _run(db, FakeWhatsApp())
        _bought(db, tenant, buyer, "Pan", at=NOON + timedelta(hours=5))
        _bought(db, tenant, late, "Pan", at=NOON + timedelta(days=5))
        quiet.opted_out_at = NOON + timedelta(hours=1)
        db.flush()

        report = campaign_report(db, campaign, money=True)
        assert report["enviados"] == 3 and report["ventas_atribuidas"] == 1
        assert report["monto_vendido"] == {"PEN": "80.00"}
        assert report["bajas"] == 1
        assert "monto_vendido" not in campaign_report(db, campaign, money=False)


class TestLifecycle:
    def test_cancel_skips_who_was_left(self, db: Session) -> None:
        tenant, _ = business(db)
        contact = customer(db, tenant)
        campaign = _campaign(db, tenant)
        service = CampaignService(db, tenant.id)
        service.schedule(campaign, now=NOON)
        from app.repositories.campaigns import CampaignRecipientRepository

        CampaignRecipientRepository(db, tenant.id).snapshot(campaign.id, [contact.id])
        service.cancel(campaign, now=NOON)
        row = _recipients(db, campaign)[contact.id]
        assert campaign.status == "canceled" and row.skip_reason == "cancelada"

    def test_only_a_draft_is_edited_or_deleted(self, db: Session) -> None:
        tenant, _ = business(db)
        customer(db, tenant)
        campaign = _campaign(db, tenant)
        service = CampaignService(db, tenant.id)
        service.schedule(campaign, now=NOON)
        with pytest.raises(CampaignError):
            service.update(campaign, {"name": "x", "template_id": campaign.template_id})
        with pytest.raises(CampaignError):
            service.delete(campaign)


class TestMetaRules2026:
    """Meta's rules since 2026-10-01 (`leo-sdd/meta-reglas-2026-10.md`)."""

    async def test_metas_marketing_cap_is_told_apart(self, db: Session) -> None:
        from app.providers.whatsapp.base import WhatsAppError

        tenant, _ = business(db)
        capped = customer(db, tenant)
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)

        class Capped(FakeWhatsApp):
            async def send_template(self, *args: Any, **kwargs: Any) -> Any:
                raise WhatsAppError("YCloud refused the message (HTTP 400): code 131049")

        await _run(db, Capped())
        row = _recipients(db, campaign)[capped.id]
        assert row.status == "failed" and row.skip_reason == "tope_meta"
        assert "WhatsApp limitó" in str(
            campaign_report(db, campaign, money=False)["motivos_omision"]
        )

    async def test_the_months_whatsapp_spend_counts_service_over_the_free_1000(
        self, db: Session
    ) -> None:
        from app.services.campaigns.spend import whatsapp_spend

        tenant, _ = business(db)
        contact = customer(db, tenant)
        thread = Conversation(
            tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
        )
        db.add(thread)
        db.flush()
        for _ in range(3):
            db.add(
                Message(
                    tenant_id=tenant.id,
                    conversation_id=thread.id,
                    direction="outbound",
                    author="agent",
                    content="Hola",
                    delivery_status="delivered",
                )
            )
        campaign = _campaign(db, tenant)
        CampaignService(db, tenant.id).schedule(campaign, now=NOON)
        await _run(db, FakeWhatsApp())
        db.flush()

        spend = whatsapp_spend(db, tenant.id)
        assert spend["mensajes_servicio"] == 3
        assert spend["gratis_restantes"] == 997
        assert spend["plantillas_marketing"] == 1
        # 3 service messages are within the free 1,000; one marketing template at 0.08085.
        assert spend["costo_estimado_usd"] == "0.08"
