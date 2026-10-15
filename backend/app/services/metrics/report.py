"""What the dashboards show (Feature 010, TASK-1003).

The business's report reads `metrics_daily` for closed days and computes today
live (RF-001), compares with the period just before, and turns conversations the
agent handled alone into "hours saved" at the business's own minutes per
conversation (HU-03). An operator gets the same report without money (RF-004).

The platform's report is about the business of Leo itself: businesses by state,
sign-ups, trial-to-paid conversion, churn, MRR, and the margin per business
(what it paid in the range against what its model calls cost), plus the
operational signals. These are live queries over small tables.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import BillingLedger, Payment, Subscription
from app.models.channel import WebhookEvent
from app.models.conversation import AgentTrace, Message
from app.models.metrics import DEFAULT_MINUTES_PER_CONVERSATION, DashboardSettings, MetricsDaily
from app.models.orders import Order, OrderItem
from app.models.tenant import Tenant
from app.models.whatsapp import OPEN_REQUEST_STATUSES, ConnectionRequest, WhatsAppNumber
from app.services.metrics.rollup import DayMetrics, bounds, compute_day, local_today, zone_of

MAX_RANGE_DAYS = 400
MAX_EXPORT_DAYS = 90
CENT = Decimal("0.01")
COUNTS = (
    "conversations",
    "conversations_agent_only",
    "conversations_with_person",
    "messages_in",
    "messages_out_agent",
    "messages_out_person",
    "first_responses",
    "first_response_seconds",
    "handovers",
    "orders_confirmed",
)


class RangeTooLong(ValueError):
    pass


@dataclass
class Totals:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTS, 0))
    handover_reasons: Counter[str] = field(default_factory=Counter)
    sales: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    cost_usd: Decimal = Decimal("0")

    def add(self, day: DayMetrics) -> None:
        for key in COUNTS:
            self.counts[key] += getattr(day, key)
        self.handover_reasons.update(day.handover_reasons)
        for currency, amount in day.sales.items():
            self.sales[currency] += Decimal(amount)
        self.cost_usd += Decimal(day.cost_usd)


def minutes_per_conversation(session: Session, tenant_id: uuid.UUID) -> int:
    settings = session.get(DashboardSettings, tenant_id)
    return settings.minutes_per_conversation if settings else DEFAULT_MINUTES_PER_CONVERSATION


def _row_to_day(row: MetricsDaily) -> DayMetrics:
    return DayMetrics(
        day=row.day,
        **{key: getattr(row, key) for key in DayMetrics(day=row.day).columns()},
    )


def days_in_range(
    session: Session, tenant_id: uuid.UUID, start: date, end: date, *, now: datetime
) -> list[DayMetrics]:
    """Every day of [start, end]: stored rows, today live, missing closed days as zeros."""
    if end < start:
        start, end = end, start
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise RangeTooLong(f"a lo más {MAX_RANGE_DAYS} días")
    zone = zone_of(session, tenant_id)
    today = local_today(now, zone)
    stored = {
        row.day: row
        for row in session.execute(
            select(MetricsDaily).where(
                MetricsDaily.tenant_id == tenant_id,
                MetricsDaily.day >= start,
                MetricsDaily.day <= end,
            )
        ).scalars()
    }
    out: list[DayMetrics] = []
    day = start
    while day <= end:
        if day >= today:
            out.append(
                compute_day(session, tenant_id, day, zone) if day == today else DayMetrics(day)
            )
        elif day in stored:
            out.append(_row_to_day(stored[day]))
        else:
            out.append(DayMetrics(day=day))
        day += timedelta(days=1)
    return out


def _summary(totals: Totals, minutes: int, *, money: bool) -> dict[str, Any]:
    c = totals.counts
    summary: dict[str, Any] = {
        **c,
        "primera_respuesta_promedio_segundos": (
            round(c["first_response_seconds"] / c["first_responses"])
            if c["first_responses"]
            else None
        ),
        "horas_ahorradas": round(c["conversations_agent_only"] * minutes / 60, 1),
        "derivaciones_por_motivo": dict(totals.handover_reasons.most_common()),
    }
    summary.pop("first_response_seconds")
    if money:
        summary["ventas"] = {k: str(v.quantize(CENT)) for k, v in sorted(totals.sales.items())}
    return summary


def tenant_report(
    session: Session,
    tenant_id: uuid.UUID,
    start: date,
    end: date,
    *,
    now: datetime,
    money: bool,
) -> dict[str, Any]:
    days = days_in_range(session, tenant_id, start, end, now=now)
    length = len(days)
    previous = days_in_range(
        session,
        tenant_id,
        days[0].day - timedelta(days=length),
        days[0].day - timedelta(days=1),
        now=now,
    )
    current_totals, previous_totals = Totals(), Totals()
    for day in days:
        current_totals.add(day)
    for day in previous:
        previous_totals.add(day)
    minutes = minutes_per_conversation(session, tenant_id)
    report: dict[str, Any] = {
        "desde": days[0].day.isoformat(),
        "hasta": days[-1].day.isoformat(),
        "minutos_por_conversacion": minutes,
        "total": _summary(current_totals, minutes, money=money),
        "anterior": _summary(previous_totals, minutes, money=money),
        "por_dia": [
            {
                "dia": d.day.isoformat(),
                "conversaciones": d.conversations,
                "solo_agente": d.conversations_agent_only,
                "con_persona": d.conversations_with_person,
                "derivaciones": d.handovers,
                "pedidos": d.orders_confirmed,
            }
            for d in days
        ],
    }
    if money:
        report["mas_vendidos"] = top_products(session, tenant_id, days[0].day, days[-1].day)
    return report


def top_products(
    session: Session, tenant_id: uuid.UUID, start: date, end: date, limit: int = 5
) -> list[dict[str, Any]]:
    zone = zone_of(session, tenant_id)
    since, _ = bounds(start, zone)
    _, until = bounds(end, zone)
    rows = session.execute(
        select(OrderItem.nombre, func.sum(OrderItem.cantidad).label("cantidad"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.tenant_id == tenant_id,
            OrderItem.tenant_id == tenant_id,
            Order.is_sandbox.is_(False),
            Order.confirmed_at >= since,
            Order.confirmed_at < until,
        )
        .group_by(OrderItem.nombre)
        .order_by(func.sum(OrderItem.cantidad).desc(), OrderItem.nombre)
        .limit(limit)
    ).all()
    return [{"producto": name, "cantidad": int(quantity)} for name, quantity in rows]


def export_csv(
    session: Session, tenant_id: uuid.UUID, start: date, end: date, *, now: datetime, money: bool
) -> str:
    if abs((end - start).days) + 1 > MAX_EXPORT_DAYS:
        raise RangeTooLong(f"a lo más {MAX_EXPORT_DAYS} días por exportación")
    days = days_in_range(session, tenant_id, start, end, now=now)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = [
        "dia", "conversaciones", "solo_agente", "con_persona", "mensajes_recibidos",
        "respuestas_agente", "respuestas_persona", "primera_respuesta_promedio_s", "derivaciones",
        "pedidos_confirmados",
    ]  # fmt: skip
    currencies = sorted({c for d in days for c in d.sales}) if money else []
    writer.writerow(header + [f"ventas_{c}" for c in currencies])
    for d in days:
        average = round(d.first_response_seconds / d.first_responses) if d.first_responses else ""
        writer.writerow(
            [
                d.day.isoformat(),
                d.conversations,
                d.conversations_agent_only,
                d.conversations_with_person,
                d.messages_in,
                d.messages_out_agent,
                d.messages_out_person,
                average,
                d.handovers,
                d.orders_confirmed,
            ]  # fmt: skip
            + [d.sales.get(c, "0.00") for c in currencies]
        )
    return buffer.getvalue()


# ----------------------------------------------------------------- platform


def platform_report(session: Session, start: date, end: date, *, now: datetime) -> dict[str, Any]:
    since = datetime.combine(start, datetime.min.time(), tzinfo=now.tzinfo)
    until = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)

    by_status: dict[str, int] = {
        str(status): int(n)
        for status, n in session.execute(
            select(Tenant.status, func.count()).group_by(Tenant.status)
        ).all()
    }
    signups = session.execute(
        select(Tenant.id, Tenant.created_at).where(
            Tenant.created_at >= since, Tenant.created_at < until
        )
    ).all()
    weeks: Counter[str] = Counter()
    for _, created in signups:
        year, week, _ = created.isocalendar()
        weeks[f"{year}-S{week:02d}"] += 1
    paying = set(
        session.execute(select(Payment.tenant_id).where(Payment.status == "approved")).scalars()
    )
    converted = sum(1 for tenant_id, _ in signups if tenant_id in paying)

    churn = session.execute(
        select(func.count())
        .select_from(BillingLedger)
        .where(
            BillingLedger.event.in_(("cancelada", "pausada_por_falta_de_pago")),
            BillingLedger.created_at >= since,
            BillingLedger.created_at < until,
        )
    ).scalar_one()

    mrr = Decimal("0")
    for price, cycle in session.execute(
        select(Subscription.price_locked_usd, Subscription.billing_cycle).where(
            Subscription.status.in_(("active", "past_due"))
        )
    ).all():
        mrr += price if cycle == "monthly" else price / 12

    revenue: dict[uuid.UUID, Decimal] = {
        tenant_id: Decimal(total)
        for tenant_id, total in session.execute(
            select(Payment.tenant_id, func.sum(Payment.amount_usd))
            .where(
                Payment.status == "approved",
                Payment.created_at >= since,
                Payment.created_at < until,
            )
            .group_by(Payment.tenant_id)
        ).all()
    }
    costs = {
        tenant_id: (Decimal(cost), int(tokens))
        for tenant_id, cost, tokens in session.execute(
            select(
                AgentTrace.tenant_id,
                func.coalesce(func.sum(AgentTrace.cost_usd), 0),
                func.coalesce(func.sum(AgentTrace.input_tokens + AgentTrace.output_tokens), 0),
            )
            .where(AgentTrace.created_at >= since, AgentTrace.created_at < until)
            .group_by(AgentTrace.tenant_id)
        ).all()
    }
    unpriced = session.execute(
        select(func.count())
        .select_from(AgentTrace)
        .where(
            AgentTrace.created_at >= since,
            AgentTrace.created_at < until,
            AgentTrace.cost_usd.is_(None),
        )
    ).scalar_one()
    names: dict[uuid.UUID, str] = {
        row[0]: row[1] for row in session.execute(select(Tenant.id, Tenant.name)).all()
    }
    margins: list[dict[str, object]] = []
    for tenant_id in set(revenue) | set(costs):
        paid = Decimal(revenue.get(tenant_id, 0))
        cost, tokens = costs.get(tenant_id, (Decimal("0"), 0))
        margins.append(
            {
                "tenant_id": str(tenant_id),
                "negocio": names.get(tenant_id, "?"),
                "ingreso_usd": str(paid.quantize(CENT)),
                "costo_llm_usd": str(cost.quantize(Decimal("0.0001"))),
                "margen_usd": str((paid - cost).quantize(CENT)),
                "tokens": tokens,
            }
        )
    margins.sort(key=lambda m: Decimal(str(m["margen_usd"])))

    p95 = session.execute(
        select(func.percentile_cont(0.95).within_group(AgentTrace.latency_ms)).where(
            AgentTrace.created_at >= since,
            AgentTrace.created_at < until,
            AgentTrace.latency_ms.is_not(None),
        )
    ).scalar_one_or_none()
    return {
        "desde": start.isoformat(),
        "hasta": end.isoformat(),
        "negocios_por_estado": by_status,
        "registros_por_semana": dict(sorted(weeks.items())),
        "registros": len(signups),
        "convertidos": converted,
        "conversion": round(converted / len(signups), 3) if signups else None,
        "bajas": int(churn),
        "mrr_usd": str(mrr.quantize(CENT)),
        "margen_por_negocio": margins,
        "trazas_sin_precio": int(unpriced),
        "operacion": {
            "conexiones_pendientes": session.execute(
                select(func.count())
                .select_from(ConnectionRequest)
                .where(ConnectionRequest.status.in_(OPEN_REQUEST_STATUSES))
            ).scalar_one(),
            "sin_procesar": session.execute(
                select(func.count())
                .select_from(WebhookEvent)
                .where(WebhookEvent.status == "dead_letter")
            ).scalar_one(),
            "envios_fallidos": session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.delivery_status == "failed",
                    Message.created_at >= since,
                    Message.created_at < until,
                )
            ).scalar_one(),
            "latencia_p95_ms": round(p95) if p95 is not None else None,
            "numeros_en_riesgo": session.execute(
                select(func.count())
                .select_from(WhatsAppNumber)
                .where(WhatsAppNumber.quality_rating.in_(("RED", "YELLOW", "red", "yellow")))
            ).scalar_one(),
        },
    }
