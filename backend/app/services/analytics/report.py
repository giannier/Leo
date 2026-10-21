"""Advanced analytics, computed live (Feature 013, C-104): the funnel, products, satisfaction.

* **Funnel** (HU-01): real conversations started in the range → with commercial intent (a lead)
  → reached "por pagar" → won, with the rates between steps, the previous period of the same
  length, and the split by origin (campaign, ad, direct), weekday and hour.
* **Products** (HU-02): units and amounts sold (confirmed orders, the honest criterion of 010
  C-001), average ticket, products asked about and never sold, and products out of stock that
  people keep asking for.
* **Satisfaction** (HU-03): average, distribution, recent comments, and the assistant against
  each person of the team (owners and admins only, 5 answers at least, C-105).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import CsatSurvey
from app.models.business import Product
from app.models.campaigns import Campaign
from app.models.conversation import REAL_CHANNELS, Conversation
from app.models.crm import Lead, LeadEvent
from app.models.orders import Order, OrderItem
from app.models.user import User
from app.process.facts import CONFIRMED_ORDER_STATES
from app.services.metrics.report import MAX_RANGE_DAYS, RangeTooLong
from app.services.metrics.rollup import bounds, zone_of

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MIN_PER_PERSON = 5
RECENT_COMMENTS = 10


def _range(
    session: Session, tenant_id: uuid.UUID, start: date, end: date
) -> tuple[datetime, datetime]:
    if end < start:
        start, end = end, start
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise RangeTooLong(f"a lo más {MAX_RANGE_DAYS} días")
    zone = zone_of(session, tenant_id)
    return bounds(start, zone)[0], bounds(end, zone)[1]


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _steps(
    session: Session, tenant_id: uuid.UUID, since: datetime, until: datetime
) -> dict[str, Any]:
    conversations = list(
        session.execute(
            select(Conversation.id, Conversation.created_at, Conversation.origin_campaign_id).where(
                Conversation.tenant_id == tenant_id,
                Conversation.channel.in_(REAL_CHANNELS),
                Conversation.created_at >= since,
                Conversation.created_at < until,
            )
        ).all()
    )
    leads = list(
        session.execute(
            select(Lead.id, Lead.conversation_id, Lead.origen, Lead.status).where(
                Lead.tenant_id == tenant_id,
                Lead.created_at >= since,
                Lead.created_at < until,
                Lead.status != "discarded",
            )
        ).all()
    )
    reached: dict[str, set[uuid.UUID]] = defaultdict(set)
    if leads:
        for lead_id, stage in session.execute(
            select(LeadEvent.lead_id, LeadEvent.to_stage).where(
                LeadEvent.tenant_id == tenant_id,
                LeadEvent.lead_id.in_([row[0] for row in leads]),
                LeadEvent.to_stage.in_(("por_pagar", "ganado")),
            )
        ).all():
            reached[str(stage)].add(lead_id)
    won = {row[0] for row in leads if row[3] == "won"} | reached["ganado"]
    paying = reached["por_pagar"] | won
    return {
        "conversations": conversations,
        "leads": leads,
        "por_pagar": paying,
        "ganado": won,
    }


def _counts(steps: dict[str, Any]) -> dict[str, Any]:
    conversations = len(steps["conversations"])
    leads = len(steps["leads"])
    paying = len(steps["por_pagar"])
    won = len(steps["ganado"])
    return {
        "conversaciones": conversations,
        "con_intencion": leads,
        "por_pagar": paying,
        "ganados": won,
        "tasa_intencion": _rate(leads, conversations),
        "tasa_por_pagar": _rate(paying, leads),
        "tasa_cierre": _rate(won, paying),
        "conversion": _rate(won, conversations),
    }


def funnel(session: Session, tenant_id: uuid.UUID, start: date, end: date) -> dict[str, Any]:
    since, until = _range(session, tenant_id, start, end)
    length = until - since
    current = _steps(session, tenant_id, since, until)
    previous = _steps(session, tenant_id, since - length, since)
    zone = zone_of(session, tenant_id)

    names = {
        row[0]: row[1]
        for row in session.execute(
            select(Campaign.id, Campaign.name).where(Campaign.tenant_id == tenant_id)
        ).all()
    }
    won_conversations = {
        row[1] for row in current["leads"] if row[0] in current["ganado"] and row[1] is not None
    }
    lead_origin = {row[1]: row[2] for row in current["leads"] if row[1] is not None}
    by_origin: dict[str, dict[str, int]] = defaultdict(lambda: {"conversaciones": 0, "ganados": 0})
    by_weekday: dict[str, dict[str, int]] = {
        day: {"conversaciones": 0, "ganados": 0} for day in WEEKDAYS
    }
    by_hour: dict[int, dict[str, int]] = {h: {"conversaciones": 0, "ganados": 0} for h in range(24)}
    for conversation_id, created_at, campaign_id in current["conversations"]:
        if campaign_id is not None:
            origin = f"Campaña: {names.get(campaign_id, 'sin nombre')}"
        elif lead_origin.get(conversation_id):
            origin = f"Anuncio: {lead_origin[conversation_id]}"
        else:
            origin = "Directo"
        local = created_at.astimezone(zone)
        won = 1 if conversation_id in won_conversations else 0
        for bucket in (
            by_origin[origin],
            by_weekday[WEEKDAYS[local.weekday()]],
            by_hour[local.hour],
        ):
            bucket["conversaciones"] += 1
            bucket["ganados"] += won
    return {
        "desde": start.isoformat(),
        "hasta": end.isoformat(),
        "actual": _counts(current),
        "anterior": _counts(previous),
        "por_origen": dict(sorted(by_origin.items(), key=lambda item: -item[1]["conversaciones"])),
        "por_dia_semana": by_weekday,
        "por_hora": {
            str(hour): value for hour, value in by_hour.items() if value["conversaciones"]
        },
    }


def products(
    session: Session, tenant_id: uuid.UUID, start: date, end: date, *, money: bool
) -> dict[str, Any]:
    since, until = _range(session, tenant_id, start, end)
    orders = list(
        session.execute(
            select(Order).where(
                Order.tenant_id == tenant_id,
                Order.is_sandbox.is_(False),
                Order.estado.in_(CONFIRMED_ORDER_STATES),
                Order.confirmed_at >= since,
                Order.confirmed_at < until,
            )
        ).scalars()
    )
    units: Counter[str] = Counter()
    amounts: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    totals: dict[str, list[Decimal]] = defaultdict(list)
    sold_names: set[str] = set()
    if orders:
        currency = {order.id: order.moneda for order in orders}
        for order in orders:
            totals[order.moneda].append(order.total or Decimal("0"))
        for item in session.execute(
            select(OrderItem).where(
                OrderItem.tenant_id == tenant_id, OrderItem.order_id.in_(list(currency))
            )
        ).scalars():
            units[item.nombre] += item.cantidad
            amounts[item.nombre][currency[item.order_id]] += item.subtotal or Decimal("0")
            sold_names.add(item.nombre.strip().lower())

    asked: Counter[str] = Counter()
    for (interest,) in session.execute(
        select(Lead.product_interest).where(
            Lead.tenant_id == tenant_id,
            Lead.created_at >= since,
            Lead.created_at < until,
            Lead.product_interest.is_not(None),
        )
    ).all():
        for name in str(interest).split(","):
            if name.strip():
                asked[name.strip()] += 1
    out_of_stock = {
        row[0].strip().lower(): row[0]
        for row in session.execute(
            select(Product.name).where(
                Product.tenant_id == tenant_id,
                Product.deleted_at.is_(None),
                Product.stock == 0,
            )
        ).all()
    }
    report: dict[str, Any] = {
        "vendidos": [
            {
                "producto": name,
                "unidades": count,
                **(
                    {"monto": {cur: str(total) for cur, total in amounts[name].items()}}
                    if money
                    else {}
                ),
            }
            for name, count in units.most_common(20)
        ],
        "pedidos": len(orders),
        "consultados_sin_venta": [
            {"producto": name, "consultas": count}
            for name, count in asked.most_common()
            if name.lower() not in sold_names
        ][:10],
        "agotados_pedidos": [
            {"producto": out_of_stock[name.lower()], "consultas": count}
            for name, count in asked.most_common()
            if name.lower() in out_of_stock
        ][:10],
    }
    if money:
        report["ticket_promedio"] = {
            cur: str((sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.01")))
            for cur, values in totals.items()
            if values
        }
    return report


def satisfaction(
    session: Session, tenant_id: uuid.UUID, start: date, end: date, *, by_person: bool
) -> dict[str, Any]:
    since, until = _range(session, tenant_id, start, end)
    surveys = list(
        session.execute(
            select(CsatSurvey)
            .where(
                CsatSurvey.tenant_id == tenant_id,
                CsatSurvey.sent_at >= since,
                CsatSurvey.sent_at < until,
            )
            .order_by(CsatSurvey.answered_at.desc().nulls_last())
        ).scalars()
    )
    scored = [s for s in surveys if s.score is not None]
    distribution = Counter(s.score for s in scored)

    def average(rows: list[CsatSurvey]) -> float | None:
        return round(sum(s.score or 0 for s in rows) / len(rows), 2) if rows else None

    report: dict[str, Any] = {
        "enviadas": len(surveys),
        "respondidas": sum(1 for s in surveys if s.status == "answered"),
        "con_nota": len(scored),
        "promedio": average(scored),
        "distribucion": {str(n): distribution.get(n, 0) for n in range(1, 6)},
        "comentarios": [
            {
                "fecha": s.answered_at.isoformat() if s.answered_at else None,
                "nota": s.score,
                "comentario": s.comment,
            }
            for s in surveys
            if s.comment
        ][:RECENT_COMMENTS],
        "asistente": {
            "respuestas": sum(1 for s in scored if s.operator_id is None),
            "promedio": average([s for s in scored if s.operator_id is None]),
        },
    }
    if by_person:
        people: dict[uuid.UUID, list[CsatSurvey]] = defaultdict(list)
        for survey in scored:
            if survey.operator_id is not None:
                people[survey.operator_id].append(survey)
        names = (
            {
                user.id: user.full_name
                for user in session.execute(select(User).where(User.id.in_(list(people)))).scalars()
            }
            if people
            else {}
        )
        report["por_persona"] = [
            {
                "nombre": names.get(user_id, "Alguien del equipo"),
                "respuestas": len(rows),
                # Fewer than five answers say nothing about a person (C-105).
                "promedio": average(rows) if len(rows) >= MIN_PER_PERSON else None,
            }
            for user_id, rows in sorted(people.items(), key=lambda item: -len(item[1]))
        ]
    return report
