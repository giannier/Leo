"""What WhatsApp (Meta, through YCloud) will charge the business this month, approximately.

Since 2026-10-01 Meta bills free-form replies inside the 24-hour window ("service" messages)
after 1,000 free a month per number, and every template by its category (C-111,
`leo-sdd/meta-reglas-2026-10.md`). Counted from what Leo sent on the real channel; the invoice
is YCloud's, so this is an estimate and says so.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from app.models.campaigns import FREE_SERVICE_MESSAGES, MessageTemplate
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.services.campaigns.service import month_start, rate_for

SENT = ("sent", "delivered", "read")


def whatsapp_spend(
    session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    start = month_start(moment)
    base = (
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.tenant_id == tenant_id,
            Conversation.channel == "whatsapp",
            Message.direction == "outbound",
            Message.delivery_status.in_(SENT),
            Message.created_at >= start,
        )
    )
    service = int(
        session.execute(base.where(Message.msg_type.notin_(("template", "event")))).scalar_one()
    )
    by_category: dict[str, int] = {"utility": 0, "marketing": 0}
    rows = session.execute(
        select(MessageTemplate.category, func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(
            MessageTemplate,
            MessageTemplate.id
            == cast(Message.payload["plantilla_id"].astext, PgUUID(as_uuid=True)),
        )
        .where(
            Message.tenant_id == tenant_id,
            Conversation.channel == "whatsapp",
            Message.msg_type == "template",
            Message.delivery_status.in_(SENT),
            Message.created_at >= start,
        )
        .group_by(MessageTemplate.category)
    ).all()
    for category, total in rows:
        by_category[str(category)] = int(total)

    tenant = session.get(Tenant, tenant_id)
    country = tenant.pais if tenant is not None else "PE"
    today = moment.date()
    billable = max(0, service - FREE_SERVICE_MESSAGES)
    cost = Decimal("0")
    known = True
    for category, count in (("service", billable), *by_category.items()):
        if not count:
            continue
        rate = rate_for(session, country, category, today)
        if rate is None:
            known = False
            continue
        cost += rate * count
    return {
        "desde": start.date().isoformat(),
        "mensajes_servicio": service,
        "gratis_restantes": max(0, FREE_SERVICE_MESSAGES - service),
        "plantillas_utilidad": by_category["utility"],
        "plantillas_marketing": by_category["marketing"],
        "costo_estimado_usd": str(cost.quantize(Decimal("0.01"))) if known else None,
    }
