"""What a campaign achieved (Feature 011, HU-03): sent, delivered, read, replies, opt-outs
and sales attributed — an order confirmed within 72 hours of the template (RF-007, C-109),
computed when the report is read, like the CRM's metrics.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaigns import ATTRIBUTION_HOURS, Campaign, CampaignRecipient
from app.models.channel import Contact
from app.models.orders import Order
from app.process.facts import CONFIRMED_ORDER_STATES
from app.services.campaigns.sender import SKIP_REASONS

REACHED = ("sent", "delivered", "read", "replied")
# Below this, an A/B side says little (spec C-004).
SMALL_SAMPLE = 30


def campaign_report(session: Session, campaign: Campaign, *, money: bool) -> dict[str, Any]:
    tenant_id = campaign.tenant_id
    rows = list(
        session.execute(
            select(CampaignRecipient).where(
                CampaignRecipient.tenant_id == tenant_id,
                CampaignRecipient.campaign_id == campaign.id,
            )
        ).scalars()
    )
    statuses = Counter(row.status for row in rows)
    skipped = Counter(
        row.skip_reason or "otro" for row in rows if row.status in ("skipped", "failed")
    )
    reached = [row for row in rows if row.status in REACHED and row.sent_at is not None]

    opted_out = 0
    if reached:
        ids = [row.contact_id for row in reached]
        first_sent = min(row.sent_at for row in reached if row.sent_at is not None)
        opted_out = len(
            session.execute(
                select(Contact.id).where(
                    Contact.tenant_id == tenant_id,
                    Contact.id.in_(ids),
                    Contact.opted_out_at >= first_sent,
                )
            )
            .scalars()
            .all()
        )

    sales = 0
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    counted: set[uuid.UUID] = set()
    if reached:
        window = {row.contact_id: row.sent_at for row in reached}
        orders = session.execute(
            select(Order).where(
                Order.tenant_id == tenant_id,
                Order.contact_id.in_(list(window)),
                Order.is_sandbox.is_(False),
                Order.estado.in_(CONFIRMED_ORDER_STATES),
                Order.confirmed_at.is_not(None),
            )
        ).scalars()
        for order in orders:
            sent_at = window.get(order.contact_id)
            if sent_at is None or order.confirmed_at is None or order.contact_id in counted:
                continue
            if sent_at <= order.confirmed_at <= sent_at + timedelta(hours=ATTRIBUTION_HOURS):
                counted.add(order.contact_id)
                sales += 1
                amounts[order.moneda or "PEN"] += order.total or Decimal("0")

    report: dict[str, Any] = {
        "destinatarios": len(rows),
        "pendientes": statuses.get("pending", 0),
        "enviados": len(reached),
        "entregados": sum(statuses.get(s, 0) for s in ("delivered", "read", "replied")),
        "leidos": sum(statuses.get(s, 0) for s in ("read", "replied")),
        "respondieron": statuses.get("replied", 0),
        "fallidos": statuses.get("failed", 0),
        "omitidos": statuses.get("skipped", 0),
        "motivos_omision": {
            SKIP_REASONS.get(reason, reason): total for reason, total in skipped.items()
        },
        "bajas": opted_out,
        "ventas_atribuidas": sales,
    }
    if campaign.template_b_id is not None:
        # A/B (Feature 013, C-106): the same numbers per template, and a warning when a side
        # is too small to say anything.
        report["variantes"] = {}
        for variant in ("A", "B"):
            mine = [row for row in rows if row.variant == variant]
            sent = [row for row in mine if row.status in REACHED and row.sent_at is not None]
            replied = sum(1 for row in mine if row.status == "replied")
            sold = sum(1 for row in sent if row.contact_id in counted)
            report["variantes"][variant] = {
                "destinatarios": len(mine),
                "enviados": len(sent),
                "respondieron": replied,
                "ventas": sold,
                "tasa_respuesta": round(replied / len(sent), 4) if sent else None,
                "tasa_venta": round(sold / len(sent), 4) if sent else None,
                "muestra_pequena": len(sent) < SMALL_SAMPLE,
            }
    if money:
        report["monto_vendido"] = {currency: str(total) for currency, total in amounts.items()}
        report["costo_estimado_usd"] = (
            str(campaign.estimated_cost_usd) if campaign.estimated_cost_usd is not None else None
        )
    return report
