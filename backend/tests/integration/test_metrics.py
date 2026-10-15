"""Dashboard metrics (Feature 010): numbers that reconcile with the raw data.

The risk the plan names is lying metrics from counting bugs, so the core test is a
reconciliation: what the daily rows add up to equals a direct count over the same
range. Days are the business's local days (Lima here), so a message at 23:30 in
Lima belongs to that day even though it is already tomorrow in UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import Payment, Subscription
from app.models.channel import Contact
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.models.metrics import MetricsDaily, ModelCost
from app.models.orders import Order, OrderItem
from app.models.tenant import Tenant
from app.services.metrics.costs import call_cost
from app.services.metrics.report import RangeTooLong, export_csv, tenant_report
from app.services.metrics.rollup import compute_day, rollup, store_day, zone_of
from app.workers.metrics import purge
from tests.integration.test_platform_admin import _admin, _register

LIMA = ZoneInfo("America/Lima")
DAY = date(2026, 9, 10)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """A local Lima time, as the aware datetime the database stores."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LIMA)


def _tenant(db: Session) -> Tenant:
    tenant = Tenant(
        name=f"Métricas {uuid.uuid4().hex[:6]}",
        status="active",
        plan_id="emprende",
        created_at=_at(DAY - timedelta(days=5), 9),
    )
    db.add(tenant)
    db.flush()
    return tenant


def _conversation(db: Session, tenant: Tenant, channel: str = "whatsapp") -> Conversation:
    contact = Contact(tenant_id=tenant.id, phone_e164=f"+519{uuid.uuid4().int % 10**8:08d}")
    db.add(contact)
    db.flush()
    conversation = Conversation(tenant_id=tenant.id, channel=channel, contact_id=contact.id)
    db.add(conversation)
    db.flush()
    return conversation


def _message(
    db: Session,
    conversation: Conversation,
    at: datetime,
    *,
    direction: str = "inbound",
    author: str = "customer",
    msg_type: str = "text",
    payload: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        direction=direction,
        author=author,
        content="hola",
        msg_type=msg_type,
        payload=payload,
        created_at=at,
    )
    db.add(message)
    db.flush()
    return message


def _order(
    db: Session,
    conversation: Conversation,
    at: datetime,
    total: str,
    *,
    moneda: str = "PEN",
    sandbox: bool = False,
    numero: int = 1,
) -> Order:
    order = Order(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        numero=numero,
        estado="confirmado",
        moneda=moneda,
        subtotal=Decimal(total),
        total=Decimal(total),
        is_sandbox=sandbox,
        confirmed_at=at,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            tenant_id=conversation.tenant_id,
            order_id=order.id,
            nombre="Mochila",
            precio_unitario=Decimal(total),
            cantidad=2,
            subtotal=Decimal(total),
        )
    )
    db.flush()
    return order


def _a_busy_day(db: Session, tenant: Tenant) -> None:
    """One local day of work, with traps on both sides of midnight."""
    alone = _conversation(db, tenant)
    _message(db, alone, _at(DAY, 9))  # first message ever: first response 30 s
    reply = _message(
        db, alone, _at(DAY, 9) + timedelta(seconds=30), direction="outbound", author="agent"
    )
    _message(db, alone, _at(DAY, 23, 30))  # 04:30 UTC tomorrow, still this local day

    helped = _conversation(db, tenant)
    _message(db, helped, _at(DAY, 10))
    _message(db, helped, _at(DAY, 10, 2), direction="outbound", author="agent")
    _message(db, helped, _at(DAY, 10, 5), direction="outbound", author="system",
             msg_type="event", payload={"evento": "tomada"})  # fmt: skip
    _message(db, helped, _at(DAY, 10, 6), direction="outbound", author="human_operator")
    db.add(
        ConversationStageEvent(
            tenant_id=tenant.id,
            conversation_id=helped.id,
            from_stage="IDENTIFICAR",
            to_stage="DERIVADO",
            from_funnel="interesado",
            to_funnel="derivado",
            reason="herramienta:derivar_a_humano",
            facts_snapshot={"derivado": {"motivo": "queja"}},
            created_at=_at(DAY, 10, 4),
        )
    )
    _order(db, helped, _at(DAY, 11), "120.50", numero=1)
    _order(db, helped, _at(DAY, 12), "30.00", moneda="USD", numero=2)

    # Not counted: the simulator, the day before and the day after.
    sandbox = _conversation(db, tenant, channel="sandbox")
    _message(db, sandbox, _at(DAY, 12))
    _order(db, sandbox, _at(DAY, 12), "999.00", sandbox=True, numero=3)
    edges = _conversation(db, tenant)
    _message(db, edges, _at(DAY, 0) - timedelta(minutes=1))
    _message(db, edges, _at(DAY + timedelta(days=1), 0, 1))

    db.add(
        AgentTrace(
            tenant_id=tenant.id,
            message_id=reply.id,
            model="gpt-5.4-mini",
            input_tokens=1200,
            output_tokens=80,
            cost_usd=Decimal("0.000420"),
            created_at=_at(DAY, 9),
        )
    )
    db.flush()


class TestCostPerCall:
    def test_the_price_in_force_when_it_happened(self, db: Session) -> None:
        model = f"modelo-{uuid.uuid4().hex[:6]}"
        db.add_all(
            [
                ModelCost(
                    model=model,
                    input_per_1k_usd=Decimal("0.000250"),
                    output_per_1k_usd=Decimal("0.002000"),
                    valid_from=_at(DAY, 0),
                ),
                ModelCost(
                    model=model,
                    input_per_1k_usd=Decimal("0.000500"),
                    output_per_1k_usd=Decimal("0.004000"),
                    valid_from=_at(DAY, 12),
                ),
            ]  # fmt: skip
        )
        db.flush()

        assert call_cost(db, model, 1000, 500, _at(DAY, 11)) == Decimal("0.001250")
        assert call_cost(db, model, 1000, 500, _at(DAY, 13)) == Decimal("0.002500")
        assert call_cost(db, model, 1000, 500, _at(DAY - timedelta(days=1), 9)) is None
        assert call_cost(db, "sin-precio", 1000, 500, _at(DAY, 13)) is None


class TestOneDay:
    def test_every_number_of_a_busy_day(self, db: Session) -> None:
        tenant = _tenant(db)
        _a_busy_day(db, tenant)

        day = compute_day(db, tenant.id, DAY, zone_of(db, tenant.id))

        assert (day.conversations, day.conversations_agent_only, day.conversations_with_person) == (
            2,
            1,
            1,
        )
        assert (day.messages_in, day.messages_out_agent, day.messages_out_person) == (3, 2, 1)
        assert (day.first_responses, day.first_response_seconds) == (2, 30 + 120)
        assert (day.handovers, day.handover_reasons) == (1, {"queja": 1})
        assert (day.orders_confirmed, day.sales) == (2, {"PEN": "120.50", "USD": "30.00"})
        assert (day.tokens_in, day.tokens_out, day.cost_usd) == (1200, 80, Decimal("0.000420"))


class TestReconciliation:
    def test_the_daily_rows_add_up_to_a_direct_count_and_recomputing_changes_nothing(
        self, db: Session
    ) -> None:
        tenant = _tenant(db)
        _a_busy_day(db, tenant)
        for offset in (1, 2, 3):
            conversation = _conversation(db, tenant)
            for hour in range(offset):
                _message(db, conversation, _at(DAY + timedelta(days=offset), 8 + hour))
        now = _at(DAY + timedelta(days=6), 12)

        rollup(db, now=now)
        first = {
            r.day: (r.messages_in, r.conversations, dict(r.sales))
            for r in db.execute(
                select(MetricsDaily).where(MetricsDaily.tenant_id == tenant.id)
            ).scalars()
        }
        rollup(db, now=now)
        second = {
            r.day: (r.messages_in, r.conversations, dict(r.sales))
            for r in db.execute(
                select(MetricsDaily).where(MetricsDaily.tenant_id == tenant.id)
            ).scalars()
        }

        start = datetime.combine(DAY - timedelta(days=5), datetime.min.time(), tzinfo=LIMA)
        end = datetime.combine(DAY + timedelta(days=6), datetime.min.time(), tzinfo=LIMA)
        direct = db.execute(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.tenant_id == tenant.id,
                Message.direction == "inbound",
                Message.msg_type != "event",
                Conversation.channel == "whatsapp",
                Message.created_at >= start,
                Message.created_at < end,
            )
        ).scalar_one()

        assert first == second
        assert sum(v[0] for v in first.values()) == direct == 3 + 2 + 1 + 2 + 3
        assert max(first) == DAY + timedelta(days=5)  # yesterday, local
        assert first[DAY][2] == {"PEN": "120.50", "USD": "30.00"}


class TestTheReport:
    def test_totals_previous_period_hours_saved_and_best_sellers(self, db: Session) -> None:
        tenant = _tenant(db)
        _a_busy_day(db, tenant)
        store_day(db, tenant.id, compute_day(db, tenant.id, DAY, zone_of(db, tenant.id)))
        now = _at(DAY + timedelta(days=1), 12)

        report = tenant_report(db, tenant.id, DAY, DAY, now=now, money=True)
        hidden = tenant_report(db, tenant.id, DAY, DAY, now=now, money=False)

        total = report["total"]
        assert total["conversations"] == 2 and total["handovers"] == 1
        assert total["primera_respuesta_promedio_segundos"] == 75
        assert total["horas_ahorradas"] == round(1 * 4 / 60, 1)
        assert total["ventas"] == {"PEN": "120.50", "USD": "30.00"}
        assert report["mas_vendidos"] == [{"producto": "Mochila", "cantidad": 4}]
        assert report["anterior"]["conversations"] == 0
        assert "ventas" not in hidden["total"] and "mas_vendidos" not in hidden

    def test_csv_and_range_limits(self, db: Session) -> None:
        tenant = _tenant(db)
        _a_busy_day(db, tenant)
        store_day(db, tenant.id, compute_day(db, tenant.id, DAY, zone_of(db, tenant.id)))
        now = _at(DAY + timedelta(days=1), 12)

        text = export_csv(db, tenant.id, DAY - timedelta(days=1), DAY, now=now, money=True)
        without = export_csv(db, tenant.id, DAY, DAY, now=now, money=False)

        lines = text.strip().splitlines()
        assert lines[0].endswith("ventas_PEN,ventas_USD")
        assert lines[2].startswith(f"{DAY.isoformat()},2,1,1,3,2,1,75,1,2,120.50,30.00")
        assert "ventas" not in without
        with pytest.raises(RangeTooLong):
            export_csv(db, tenant.id, DAY - timedelta(days=90), DAY, now=now, money=True)


class TestTheEndpoints:
    async def test_business_report_settings_and_operator_without_money(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        from tests.integration.test_orders_api import operator_for

        account, owner, operator = await operator_for(api, mail, db)
        await api.put(
            "/api/v1/tenant/metrics/settings", headers=owner, json={"minutos_por_conversacion": 6}
        )

        mine = (await api.get("/api/v1/tenant/metrics", headers=owner)).json()
        theirs = (await api.get("/api/v1/tenant/metrics", headers=operator)).json()
        csv = await api.get("/api/v1/tenant/metrics/export.csv", headers=owner)
        refused = await api.put(
            "/api/v1/tenant/metrics/settings",
            headers=operator,
            json={"minutos_por_conversacion": 1},
        )
        too_long = await api.get(
            "/api/v1/tenant/metrics?desde=2024-01-01&hasta=2026-01-01", headers=owner
        )

        assert mine["minutos_por_conversacion"] == 6 and "ventas" in mine["total"]
        assert len(mine["por_dia"]) == 30
        assert "ventas" not in theirs["total"]
        assert csv.status_code == 200 and csv.text.startswith("dia,conversaciones")
        assert refused.status_code == 403 and too_long.status_code == 400

    async def test_platform_report_and_model_costs(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        admin, _ = await _admin(api, mail, db)
        owner, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get("/api/v1/tenant", headers=owner)).json()["id"])
        now = datetime.now(UTC)
        from app.models.billing import Charge

        charge = Charge(tenant_id=tenant_id, kind="subscription", plan_id="crece",
                        billing_cycle="annual", list_amount_usd=Decimal("566.40"),
                        amount_usd=Decimal("566.40"),
                        reference=f"LEO-T{uuid.uuid4().hex[:5].upper()}",
                        status="paid")  # fmt: skip
        db.add(charge)
        db.add(
            Subscription(
                tenant_id=tenant_id,
                plan_id="crece",
                status="active",
                billing_cycle="annual",
                price_locked_usd=Decimal("566.40"),
                current_period_start=now,
                current_period_end=now + timedelta(days=365),
            )  # fmt: skip
        )
        db.flush()
        db.add(
            Payment(
                tenant_id=tenant_id,
                charge_id=charge.id,
                provider="manual",
                provider_payment_id=f"manual:{uuid.uuid4().hex}",
                kind="subscription",
                amount_usd=Decimal("566.40"),
                status="approved",
                receipt_number=f"T-{uuid.uuid4().hex[:8]}",
            )  # fmt: skip
        )
        db.flush()

        model = f"modelo-{uuid.uuid4().hex[:6]}"
        set_cost = await api.put(
            "/api/v1/platform-admin/model-costs",
            headers=admin,
            json={"modelo": model, "entrada_por_1k_usd": "0.00025", "salida_por_1k_usd": "0.002"},
        )
        costs = (await api.get("/api/v1/platform-admin/model-costs", headers=admin)).json()
        report = (await api.get("/api/v1/platform-admin/metrics", headers=admin)).json()
        refused = await api.get("/api/v1/platform-admin/metrics", headers=owner)

        assert set_cost.status_code == 200 and model in {c["modelo"] for c in costs}
        assert Decimal(report["mrr_usd"]) >= Decimal("47.20")  # 566.40 / 12
        ours = next(m for m in report["margen_por_negocio"] if m["tenant_id"] == str(tenant_id))
        assert ours["ingreso_usd"] == "566.40" and ours["margen_usd"] == "566.40"
        assert report["convertidos"] >= 1 and "latencia_p95_ms" in report["operacion"]
        assert refused.status_code == 404


class TestRetention:
    def test_old_metrics_and_traces_are_purged(self, db: Session) -> None:
        tenant = _tenant(db)
        conversation = _conversation(db, tenant)
        now = datetime(2027, 6, 1, tzinfo=UTC)
        old = _message(
            db, conversation, now - timedelta(days=200), direction="outbound", author="agent"
        )
        new = _message(
            db, conversation, now - timedelta(days=10), direction="outbound", author="agent"
        )
        for message in (old, new):
            db.add(AgentTrace(tenant_id=tenant.id, message_id=message.id, model="m",
                              created_at=message.created_at))  # fmt: skip
        db.add(MetricsDaily(tenant_id=tenant.id, day=(now - timedelta(days=800)).date()))
        db.add(MetricsDaily(tenant_id=tenant.id, day=(now - timedelta(days=10)).date()))
        db.flush()

        purge(db, now=now)

        traces = db.execute(select(AgentTrace.message_id).where(AgentTrace.tenant_id == tenant.id))
        days = db.execute(select(MetricsDaily.day).where(MetricsDaily.tenant_id == tenant.id))
        assert list(traces.scalars()) == [new.id]
        assert list(days.scalars()) == [(now - timedelta(days=10)).date()]
