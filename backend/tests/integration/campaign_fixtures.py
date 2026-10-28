"""Shared pieces for the Feature 011 tests: a business with a number, customers, templates."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.business import AgentConfig, BusinessProfile
from app.models.campaigns import ContactOptin, MessageTemplate
from app.models.channel import Contact
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.whatsapp.base import (
    Button,
    NumberCredentials,
    Recipient,
    SendResult,
    TemplateSpec,
    TemplateSubmitted,
    WhatsAppError,
)
from app.repositories.campaigns import ContactTagRepository
from app.services.campaigns.templates import TemplateService

WABA = "102290129340398"


class FakeWhatsApp:
    """Records what would have gone out; `fail_for` refuses templates to those phones."""

    def __init__(self) -> None:
        self.texts: list[tuple[str | None, str]] = []
        self.buttons: list[tuple[str | None, str, list[str]]] = []
        self.templates: list[dict[str, Any]] = []
        self.submitted: list[TemplateSpec] = []
        self.submit_status = "pending"
        self.fail_for: set[str] = set()

    async def send_text(
        self, credentials: NumberCredentials, *, sender: str, to: Recipient, body: str
    ) -> SendResult:
        self.texts.append((to.phone, body))
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")

    async def send_buttons(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        body: str,
        buttons: list[Button],
        header: str | None = None,
        footer: str | None = None,
    ) -> SendResult:
        self.buttons.append((to.phone, body, [b.id for b in buttons]))
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")

    async def send_template(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        name: str,
        language: str,
        values: list[str],
        header_image_link: str | None = None,
    ) -> SendResult:
        if to.phone in self.fail_for:
            raise WhatsAppError("refused")
        self.templates.append(
            {"to": to.phone, "name": name, "values": values, "image": header_image_link}
        )
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")

    async def submit_template(
        self, credentials: NumberCredentials, template: TemplateSpec
    ) -> TemplateSubmitted:
        self.submitted.append(template)
        return TemplateSubmitted(provider_template_id="9001", status=self.submit_status)


def business(
    db: Session,
    *,
    industry: str = "retail",
    quality: str | None = None,
    plan: str = "trial",
    waba: str = WABA,
    name: str = "Panadería Rosa",
) -> tuple[Tenant, WhatsAppNumber]:
    tenant = Tenant(
        name=f"Negocio {uuid.uuid4().hex[:6]}",
        status="trial",
        plan_id=plan,
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(tenant)
    db.flush()
    db.add_all(
        [
            BusinessProfile(tenant_id=tenant.id, business_name=name, industry=industry),
            AgentConfig(tenant_id=tenant.id, is_active=True, process_template_key="productos"),
        ]
    )
    number = WhatsAppNumber(
        tenant_id=tenant.id,
        phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
        display_name=name,
        provider="ycloud",
        status="connected",
        quality_rating=quality,
        credentials_enc=encrypt_secret(
            json.dumps(
                {"waba_id": waba, "phone_number_id": "112233445566778", "api_key": "yc_live_abc123"}
            )
        ),
    )
    db.add(number)
    db.flush()
    return tenant, number


def customer(
    db: Session,
    tenant: Tenant,
    *,
    name: str | None = "Ana",
    phone: str | None = None,
    wrote_hours_ago: float = 1,
    permission: str | None = "granted",
    tags: list[str] | None = None,
    opted_out: bool = False,
    now: datetime | None = None,
) -> Contact:
    # `now`: the clock the test runs the campaign at. Without it «10 days ago» counted from the real
    # day drifted toward a campaign run at a fixed date, and the result changed with the calendar.
    now = now or datetime.now(UTC)
    contact = Contact(
        tenant_id=tenant.id,
        phone_e164=phone or f"+5198{uuid.uuid4().int % 10**7:07d}",
        profile_name=name,
        custom_data={"nombres": name} if name else {},
        last_inbound_at=now - timedelta(hours=wrote_hours_ago),
        opted_out_at=now if opted_out else None,
    )
    db.add(contact)
    db.flush()
    if permission is not None:
        db.add(
            ContactOptin(
                tenant_id=tenant.id,
                contact_id=contact.id,
                source="conversation",
                status=permission,
                evidence={},
            )
        )
    if tags:
        ContactTagRepository(db, tenant.id).add_tags(contact.id, tags)
    db.flush()
    return contact


def template(
    db: Session,
    tenant: Tenant,
    *,
    body: str = "Hola {{nombre}}, en {{negocio}} tenemos 2x1 esta semana.",
    category: str = "marketing",
    status: str = "approved",
    label: str = "Promo de julio",
) -> MessageTemplate:
    created = TemplateService(db, tenant.id).create(
        {"label": label, "category": category, "body": body}
    )
    created.status = status
    db.flush()
    return created
