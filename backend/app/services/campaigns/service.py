"""Campaigns: build, estimate, schedule, send in batches, pause (Feature 011, HU-03).

**Scheduling** checks what can be checked once: the template is approved, the number is
connected and healthy, the segment reaches somebody, and the plan's caps (C-108; with billing
off they are warnings, `billing_enforced()`).

**Sending** (`CampaignRunner.tick`, every minute) checks what can change at any moment:
the business's local hours (C-003), the number (connected, quality, C-002), the template
(still approved), and the daily cap of the number (C-106). For each recipient, at the moment
of sending: permission and BAJA again (RF-002), the values of the variables (C-004). A
recipient is in a campaign once (RF-004) and its row is locked while it is sent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.business import BusinessProfile
from app.models.campaigns import (
    DEFAULT_DAILY_LIMIT,
    Campaign,
    MessageTemplate,
    MetaRate,
)
from app.models.channel import Contact
from app.models.conversation import Message
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.providers.storage.base import StorageProvider
from app.providers.whatsapp.base import WhatsAppProvider
from app.repositories.campaigns import (
    CampaignRecipientRepository,
    CampaignRepository,
    ContactOptinRepository,
    MessageTemplateRepository,
)
from app.services.campaigns import CampaignError
from app.services.campaigns.hooks import STOP_QUALITY
from app.services.campaigns.segments import Segment, contact_ids, count
from app.services.campaigns.sender import TemplateSender, connected_number, fill
from app.services.followups import local_time
from app.services.trial import billing_enforced

logger = logging.getLogger(__name__)

# Recipients per campaign per minute (C-106).
BATCH = 30
EARLIEST = time(8, 0)
LATEST = time(21, 0)
OPEN_STATES = ("draft", "scheduled", "sending", "paused")


def parse_hour(value: str) -> time:
    try:
        hours, minutes = (int(part) for part in value.split(":"))
        return time(hours, minutes)
    except (ValueError, TypeError) as exc:
        raise CampaignError("horario_invalido", "Usa horas como 09:00.") from exc


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def rate_for(session: Session, country: str, category: str, today: date) -> Decimal | None:
    return session.execute(
        select(MetaRate.rate_usd)
        .where(
            MetaRate.country == country,
            MetaRate.category == category,
            MetaRate.valid_from <= today,
        )
        .order_by(MetaRate.valid_from.desc())
        .limit(1)
    ).scalar_one_or_none()


class CampaignService:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self.campaigns = CampaignRepository(session, tenant_id)
        self.recipients = CampaignRecipientRepository(session, tenant_id)

    # --------------------------------------------------------------- building

    def template(self, template_id: uuid.UUID | None) -> MessageTemplate:
        template = (
            MessageTemplateRepository(self._session, self._tenant_id).get(template_id)
            if template_id is not None
            else None
        )
        if template is None:
            raise CampaignError("sin_plantilla", "Elige una plantilla.")
        return template

    def _clean(self, values: dict[str, Any]) -> dict[str, Any]:
        name = str(values.get("name") or "").strip()
        if not 1 <= len(name) <= 80:
            raise CampaignError(
                "nombre_invalido", "Ponle a la campaña un nombre de hasta 80 letras."
            )
        template = self.template(values.get("template_id"))
        window_from = str(values.get("window_from") or "09:00")
        window_to = str(values.get("window_to") or "20:00")
        start, end = parse_hour(window_from), parse_hour(window_to)
        if not (EARLIEST <= start < end <= LATEST):
            raise CampaignError(
                "horario_invalido", "El horario de envío debe estar entre 08:00 y 21:00."
            )
        scheduled_at = values.get("scheduled_at")
        return {
            "name": name,
            "template_id": template.id,
            "segment": Segment.parse(values.get("segment")).as_dict(),
            "window_from": start.strftime("%H:%M"),
            "window_to": end.strftime("%H:%M"),
            "scheduled_at": scheduled_at,
        }

    def create(self, values: dict[str, Any], *, by: uuid.UUID | None) -> Campaign:
        return self.campaigns.add(created_by=by, **self._clean(values))

    def update(self, campaign: Campaign, values: dict[str, Any]) -> Campaign:
        if campaign.status != "draft":
            raise CampaignError("no_editable", "Solo se edita una campaña en borrador.", status=409)
        for field, value in self._clean(values).items():
            setattr(campaign, field, value)
        self._session.flush()
        return campaign

    def delete(self, campaign: Campaign) -> None:
        if campaign.status != "draft":
            raise CampaignError(
                "no_borrable", "Solo se borra un borrador; cancela la campaña.", status=409
            )
        self._session.delete(campaign)
        self._session.flush()

    # ------------------------------------------------------------- estimating

    def estimate(
        self, segment: Segment, template: MessageTemplate, *, now: datetime
    ) -> dict[str, Any]:
        tenant = self._session.get(Tenant, self._tenant_id)
        country = tenant.pais if tenant is not None else "PE"
        people = count(self._session, self._tenant_id, segment, now=now)
        rate = rate_for(self._session, country, template.category, now.date())
        warnings = self._limits(people, now=now)
        return {
            "destinatarios": people,
            "tarifa_usd": str(rate) if rate is not None else None,
            "costo_estimado_usd": str((rate * people).quantize(Decimal("0.01")))
            if rate is not None
            else None,
            "pais": country,
            "categoria": template.category,
            "avisos": warnings,
            "limites_aplicados": billing_enforced(),
        }

    def _limits(self, people: int, *, now: datetime) -> list[str]:
        tenant = self._session.get(Tenant, self._tenant_id)
        plan = self._session.get(Plan, tenant.plan_id) if tenant is not None else None
        if plan is None:
            return []
        warnings = []
        used = self.campaigns.started_since(month_start(now))
        if plan.max_campaigns_month and used >= plan.max_campaigns_month:
            warnings.append(
                f"Tu plan incluye {plan.max_campaigns_month} campaña(s) al mes y ya "
                f"enviaste {used}."
            )
        if plan.max_recipients_per_campaign and people > plan.max_recipients_per_campaign:
            warnings.append(
                f"Tu plan permite {plan.max_recipients_per_campaign} destinatarios por campaña."
            )
        return warnings

    # ------------------------------------------------------------- lifecycle

    def schedule(self, campaign: Campaign, *, now: datetime) -> Campaign:
        if campaign.status != "draft":
            raise CampaignError("no_programable", "Esta campaña ya se programó.", status=409)
        template = self.template(campaign.template_id)
        if template.status != "approved":
            raise CampaignError(
                "plantilla_no_aprobada", "La plantilla aún no está aprobada por Meta.", status=409
            )
        number, _ = connected_number(self._session, self._tenant_id)
        if (number.quality_rating or "").upper() in STOP_QUALITY:
            raise CampaignError(
                "calidad_baja",
                "La calidad de tu número bajó; espera a que se recupere antes de enviar campañas.",
                status=409,
            )
        segment = Segment.parse(campaign.segment)
        estimate = self.estimate(segment, template, now=now)
        if estimate["destinatarios"] == 0:
            raise CampaignError(
                "sin_destinatarios",
                "Nadie cumple el filtro (recuerda que solo reciben quienes aceptaron promociones).",
            )
        if estimate["avisos"] and billing_enforced():
            raise CampaignError("limite_plan", " ".join(estimate["avisos"]), status=409)
        campaign.status = "scheduled"
        campaign.scheduled_at = campaign.scheduled_at or now
        campaign.pause_reason = None
        if estimate["costo_estimado_usd"] is not None:
            campaign.estimated_cost_usd = Decimal(estimate["costo_estimado_usd"])
        self._session.flush()
        return campaign

    def pause(self, campaign: Campaign) -> Campaign:
        if campaign.status not in ("scheduled", "sending"):
            raise CampaignError("no_pausable", "Esta campaña no se está enviando.", status=409)
        campaign.status = "paused"
        campaign.pause_reason = "persona"
        self._session.flush()
        return campaign

    def resume(self, campaign: Campaign) -> Campaign:
        if campaign.status != "paused":
            raise CampaignError("no_pausada", "Esta campaña no está en pausa.", status=409)
        number, _ = connected_number(self._session, self._tenant_id)
        if (number.quality_rating or "").upper() in STOP_QUALITY:
            raise CampaignError(
                "calidad_baja",
                "La calidad de tu número sigue baja; reanuda cuando se recupere.",
                status=409,
            )
        campaign.status = "sending" if campaign.started_at is not None else "scheduled"
        campaign.pause_reason = None
        self._session.flush()
        return campaign

    def cancel(self, campaign: Campaign, *, now: datetime) -> Campaign:
        if campaign.status not in ("scheduled", "sending", "paused"):
            raise CampaignError("no_cancelable", "Esta campaña ya terminó.", status=409)
        self.recipients.skip_all_pending(campaign.id, "cancelada")
        campaign.status = "canceled"
        campaign.finished_at = now
        self._session.flush()
        return campaign


class CampaignRunner:
    """One minute of sending, for every business (the ARQ cron calls `tick`)."""

    def __init__(
        self,
        session: Session,
        *,
        provider: WhatsAppProvider,
        storage: StorageProvider | None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._storage = storage
        self._now = now or datetime.now(UTC)

    def due(self) -> list[Campaign]:
        statement = (
            select(Campaign)
            .where(
                (Campaign.status == "sending")
                | ((Campaign.status == "scheduled") & (Campaign.scheduled_at <= self._now))
            )
            .order_by(Campaign.scheduled_at, Campaign.id)
        )
        return list(self._session.execute(statement).scalars())

    async def tick(self) -> int:
        sent = 0
        for campaign in self.due():
            try:
                sent += await self.run(campaign)
            except CampaignError as exc:
                self._stop(campaign, exc.code)
            self._session.commit()
        return sent

    def _stop(self, campaign: Campaign, reason: str) -> None:
        campaign.status = "paused"
        campaign.pause_reason = reason
        self._session.flush()
        logger.warning(
            "a campaign was paused",
            extra={"tenant_id": str(campaign.tenant_id), "reason": reason},
        )

    def _in_hours(self, campaign: Campaign) -> bool:
        profile = self._session.get(BusinessProfile, campaign.tenant_id)
        local = local_time(self._now, profile.timezone if profile is not None else None)
        return parse_hour(campaign.window_from) <= local < parse_hour(campaign.window_to)

    def _snapshot(self, campaign: Campaign) -> None:
        tenant = self._session.get(Tenant, campaign.tenant_id)
        plan = self._session.get(Plan, tenant.plan_id) if tenant is not None else None
        cap = (
            plan.max_recipients_per_campaign
            if plan is not None and plan.max_recipients_per_campaign and billing_enforced()
            else None
        )
        ids = contact_ids(
            self._session,
            campaign.tenant_id,
            Segment.parse(campaign.segment),
            now=self._now,
            limit=cap,
        )
        CampaignRecipientRepository(self._session, campaign.tenant_id).snapshot(campaign.id, ids)
        campaign.status = "sending"
        campaign.started_at = self._now
        self._session.flush()

    def _budget(self, campaign: Campaign, limit: int | None) -> int:
        """Templates the number may still send in the last 24 hours (C-106)."""
        used = self._session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.tenant_id == campaign.tenant_id,
                Message.msg_type == "template",
                Message.direction == "outbound",
                Message.created_at >= self._now - timedelta(hours=24),
            )
        ).scalar_one()
        return max(0, (limit or DEFAULT_DAILY_LIMIT) - int(used))

    async def run(self, campaign: Campaign) -> int:
        tenant_id = campaign.tenant_id
        if campaign.status == "scheduled":
            self._snapshot(campaign)
        recipients = CampaignRecipientRepository(self._session, tenant_id)
        if recipients.remaining(campaign.id) == 0:
            campaign.status = "completed"
            campaign.finished_at = self._now
            self._session.flush()
            return 0
        if not self._in_hours(campaign):
            return 0
        number, _ = connected_number(self._session, tenant_id)
        if (number.quality_rating or "").upper() in STOP_QUALITY:
            raise CampaignError("calidad", "quality")
        template = MessageTemplateRepository(self._session, tenant_id).get(campaign.template_id)
        if template is None or template.status != "approved":
            raise CampaignError("plantilla", "template")
        budget = min(BATCH, self._budget(campaign, number.limite_diario))
        if budget == 0:
            return 0

        sender = TemplateSender(
            self._session, tenant_id, provider=self._provider, storage=self._storage, now=self._now
        )
        optins = ContactOptinRepository(self._session, tenant_id)
        sent = 0
        for recipient in recipients.pending(campaign.id, budget):
            contact = self._session.get(Contact, recipient.contact_id)
            optin = optins.for_contact(recipient.contact_id)
            reason = None
            if contact is None or contact.opted_out_at is not None:
                reason = "baja"
            elif optin is None or optin.status != "granted":
                reason = "sin_permiso"
            elif not (contact.phone_e164 or contact.external_user_id):
                reason = "sin_telefono"
            filled = fill(self._session, tenant_id, template, contact) if contact else None
            if reason is None and filled is not None and filled.skip:
                reason = filled.skip
            if reason is not None or contact is None or filled is None:
                recipient.status = "skipped"
                recipient.skip_reason = reason or "baja"
                continue
            message = await sender.send(
                contact, template, filled, kind="campana", campaign_id=campaign.id
            )
            recipient.message_id = message.id
            recipient.conversation_id = message.conversation_id
            if message.delivery_status == "failed":
                recipient.status = "failed"
                recipient.skip_reason = "envio_fallido"
            else:
                recipient.status = "sent"
                recipient.sent_at = self._now
                sent += 1
            self._session.flush()
        if recipients.remaining(campaign.id) == 0:
            campaign.status = "completed"
            campaign.finished_at = self._now
            self._session.flush()
        return sent
