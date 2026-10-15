"""Billing (Feature 009): the whole cycle, to the cent, with a simulated clock.

Payments are recorded by hand in v1 (Giannier, 2026-10-14), so "the webhook" here
is the admin's `POST /platform-admin/billing/payments`: same effect, idempotent
by the operation number. The calendar jobs are driven with explicit `now`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.billing import BillingLedger, Charge, Coupon, Subscription, UsagePeriod
from app.models.business import AgentConfig
from app.models.knowledge import KnowledgeSource
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.services.billing.jobs import close_periods, dun, issue_renewals
from app.services.billing.money import add_months
from app.services.billing.service import (
    AmountMismatch,
    BillingService,
    ChargeNotPending,
    CouponInvalid,
    DowngradeBlocked,
    UseChangePlan,
)
from app.services.trial import agent_may_reply, record_ai_message
from tests.integration.test_platform_admin import _admin, _register

B = "/api/v1/tenant/billing"
ADMIN = "/api/v1/platform-admin/billing"
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def only_this_tests_subscriptions(db: Session) -> None:
    """The calendar jobs walk every business; a dev database may hold others (an
    e2e run's). They are set aside inside this test's transaction, undone after."""
    db.execute(update(Subscription).values(status="canceled"))
    db.flush()


async def _business(api: AsyncClient, mail: Any, db: Session) -> tuple[uuid.UUID, dict[str, str]]:
    headers, _ = await _register(api, mail, db)
    tenant_id = uuid.UUID((await api.get("/api/v1/tenant", headers=headers)).json()["id"])
    return tenant_id, headers


def _pay(service: BillingService, charge: Charge, operation: str | None = None) -> Any:
    return service.apply_payment(
        reference=charge.reference,
        provider="manual",
        provider_payment_id=f"manual:{operation or uuid.uuid4().hex}",
        amount_usd=charge.amount_usd,
    )


def _subscribe(
    db: Session,
    tenant_id: uuid.UUID,
    plan: str = "emprende",
    cycle: str = "monthly",
    now: datetime = T0,
) -> Subscription:
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    tenant.trial_ends_at = now  # no trial days left: the period is exactly one cycle
    service = BillingService(db, tenant_id, now=now)
    _pay(service, service.checkout(plan, cycle))
    subscription = service.subscriptions.current()
    assert subscription is not None
    return subscription


def _ledger(db: Session, tenant_id: uuid.UUID) -> list[str]:
    return [
        row.event
        for row in db.execute(
            select(BillingLedger)
            .where(BillingLedger.tenant_id == tenant_id)
            .order_by(BillingLedger.id)
        ).scalars()
    ]


class TestTrialToPaid:
    async def test_checkout_coupon_manual_payment_and_trial_days_kept(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner = await _business(api, mail, db)
        admin, _ = await _admin(api, mail, db)
        made = await api.post(
            f"{ADMIN}/coupons", headers=admin, json={"codigo": "piloto15", "porcentaje": 15}
        )
        assert made.status_code == 201
        trial_end = db.get(Tenant, tenant_id).trial_ends_at  # type: ignore[union-attr]

        charge = (
            await api.post(
                f"{B}/checkout", headers=owner, json={"plan_id": "emprende", "cupon": "PILOTO15"}
            )
        ).json()

        assert (charge["lista"], charge["descuento"], charge["monto"]) == ("29.00", "4.35", "24.65")
        assert charge["referencia"].startswith("LEO-") and charge["estado"] == "pending"

        body = {"referencia": charge["referencia"], "operacion": "BCP-778899", "monto_usd": "24.65"}
        paid = await api.post(f"{ADMIN}/payments", headers=admin, json=body)
        again = await api.post(f"{ADMIN}/payments", headers=admin, json=body)

        assert paid.status_code == 201 and paid.json()["comprobante"].startswith("LEO-")
        assert again.status_code == 201 and again.json()["repetido"] is True
        assert again.json()["id"] == paid.json()["id"]
        db.expire_all()
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None and (tenant.status, tenant.plan_id) == ("active", "emprende")
        overview = (await api.get(B, headers=owner)).json()
        sub = overview["suscripcion"]
        assert sub["estado"] == "active" and sub["precio"] == "29.00"
        # C-005: the trial days left were added to the first month.
        end = datetime.fromisoformat(sub["hasta"])
        start = datetime.fromisoformat(sub["desde"])
        assert trial_end is not None
        assert abs((end - add_months(start, 1)) - (trial_end - start)) < timedelta(seconds=5)
        assert overview["consumo"]["limite"] == 1000 and overview["consumo"]["usados"] == 0
        assert overview["pendientes"] == [] and len(overview["pagos"]) == 1
        assert _ledger(db, tenant_id) == ["cobro", "pago", "periodo"]

        # One use per business.
        reused = await api.post(
            f"{B}/checkout", headers=owner, json={"plan_id": "crece", "cupon": "PILOTO15"}
        )
        assert reused.status_code == 409  # already active: change plan instead

    async def test_the_receipt_is_a_pdf_with_its_number(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner = await _business(api, mail, db)
        _subscribe(db, tenant_id)
        payment = (await api.get(B, headers=owner)).json()["pagos"][0]

        pdf = await api.get(f"{B}/payments/{payment['id']}/receipt.pdf", headers=owner)

        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        text_ = PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
        assert payment["comprobante"] in text_ and "US$ 29.00" in text_
        assert "Plan: Emprende mensual" in text_

    async def test_a_wrong_amount_or_a_paid_charge_is_refused(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        service = BillingService(db, tenant_id, now=T0)
        charge = service.checkout("crece", "annual")
        assert charge.amount_usd == Decimal("566.40")

        with pytest.raises(AmountMismatch):
            service.apply_payment(
                reference=charge.reference,
                provider="manual",
                provider_payment_id="manual:x1",
                amount_usd=Decimal("566.39"),
            )
        _pay(service, charge)
        with pytest.raises(ChargeNotPending):
            _pay(service, charge)

    async def test_an_invalid_coupon_says_why(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        db.add(Coupon(code="VIEJO", percent_off=10, expires_at=T0 - timedelta(days=1)))
        db.add(Coupon(code="LLENO", percent_off=10, max_redemptions=0))
        db.flush()
        service = BillingService(db, tenant_id, now=T0)

        for code, why in [("NOEXISTE", "no existe"), ("VIEJO", "venció"), ("LLENO", "usos")]:
            with pytest.raises(CouponInvalid, match=why):
                service.checkout("emprende", "monthly", coupon=code)

    async def test_a_100_percent_coupon_activates_without_a_payment(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        db.add(Coupon(code="GRATIS", percent_off=100))
        db.flush()

        charge = BillingService(db, tenant_id, now=T0).checkout("pro", "monthly", coupon="GRATIS")

        assert charge.amount_usd == Decimal("0.00") and charge.status == "paid"
        assert db.get(Tenant, tenant_id).status == "active"  # type: ignore[union-attr]


class TestTheCalendar:
    async def test_renewal_grace_reminders_pause_and_reactivation(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        subscription = _subscribe(db, tenant_id)
        end = subscription.current_period_end
        assert end == add_months(T0, 1)

        assert issue_renewals(db, now=end - timedelta(days=8)) == []
        notices = issue_renewals(db, now=end - timedelta(days=7))
        assert issue_renewals(db, now=end - timedelta(days=6)) == []  # only once
        renewal = db.execute(
            select(Charge).where(Charge.tenant_id == tenant_id, Charge.kind == "renewal")
        ).scalar_one()
        assert renewal.amount_usd == Decimal("29.00") and renewal.period_start == end
        assert [n.subject for n in notices] == ["Tu plan de Leo se renueva pronto"]
        assert renewal.reference in (notices[0].text or "")

        close_periods(db, now=end + timedelta(minutes=1))
        assert subscription.status == "past_due"
        assert subscription.grace_until == end + timedelta(days=5)
        # The agent keeps answering during the grace.
        assert db.get(Tenant, tenant_id).status == "active"  # type: ignore[union-attr]

        reminders = []
        for day in (0, 1, 2, 3, 3, 4, 5):
            reminders += dun(db, now=end + timedelta(days=day, hours=1))
        # Day 1 and day 3 remind; day 5 is the end of the grace: the pause.
        assert [r.subject for r in reminders] == [
            "Tu pago de Leo está pendiente",
            "Tu pago de Leo está pendiente",
            "Pausamos tu agente por un pago pendiente",
        ]
        assert subscription.status == "paused"
        assert db.get(Tenant, tenant_id).status == "paused"  # type: ignore[union-attr]

        # Paying the renewal reactivates at once, with a new period from that day.
        later = end + timedelta(days=9)
        _pay(BillingService(db, tenant_id, now=later), renewal)
        assert subscription.status == "active"
        assert subscription.current_period_start == later
        assert db.get(Tenant, tenant_id).status == "active"  # type: ignore[union-attr]
        assert "pausada_por_falta_de_pago" in _ledger(db, tenant_id)

    async def test_paying_during_the_grace_keeps_the_calendar(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        subscription = _subscribe(db, tenant_id)
        end = subscription.current_period_end
        issue_renewals(db, now=end - timedelta(days=1))
        close_periods(db, now=end + timedelta(hours=1))
        renewal = db.execute(
            select(Charge).where(Charge.tenant_id == tenant_id, Charge.kind == "renewal")
        ).scalar_one()

        _pay(BillingService(db, tenant_id, now=end + timedelta(days=2)), renewal)

        assert subscription.status == "active"
        assert subscription.current_period_start == end
        assert subscription.current_period_end == add_months(end, 1)

    async def test_cancel_at_the_end_of_the_period(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        subscription = _subscribe(db, tenant_id)
        service = BillingService(db, tenant_id, now=T0 + timedelta(days=3))
        service.cancel()
        service.resume()
        service.cancel()

        assert issue_renewals(db, now=subscription.current_period_end - timedelta(days=2)) == []
        close_periods(db, now=subscription.current_period_end)

        assert subscription.status == "canceled"
        assert db.get(Tenant, tenant_id).status == "paused"  # type: ignore[union-attr]
        events = _ledger(db, tenant_id)
        assert events.count("cancelacion_programada") == 2 and "cancelada" in events

    async def test_with_billing_off_a_period_rolls_over_and_nobody_is_asked(
        self, db: Session, api: AsyncClient, mail: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings

        tenant_id, _ = await _business(api, mail, db)
        subscription = _subscribe(db, tenant_id)
        end = subscription.current_period_end
        monkeypatch.setattr(get_settings(), "billing_enforced", False)

        assert issue_renewals(db, now=end - timedelta(days=2)) == []
        close_periods(db, now=end + timedelta(hours=1))
        assert dun(db, now=end + timedelta(days=9)) == []

        assert subscription.status == "active"
        assert subscription.current_period_start == end
        assert db.get(Tenant, tenant_id).status == "active"  # type: ignore[union-attr]


class TestChangingPlans:
    async def test_an_upgrade_credits_the_unused_days_to_the_cent(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        subscription = _subscribe(db, tenant_id)  # Emprende, 1 Nov to 1 Dec: 30 days
        middle = T0 + timedelta(days=15)
        service = BillingService(db, tenant_id, now=middle)

        charge = service.change_plan("crece", "monthly")

        assert isinstance(charge, Charge)
        assert (charge.list_amount_usd, charge.credit_usd, charge.amount_usd) == (
            Decimal("59.00"),
            Decimal("14.50"),
            Decimal("44.50"),
        )
        _pay(service, charge)
        assert (subscription.plan_id, subscription.current_period_start) == ("crece", middle)
        assert db.get(Tenant, tenant_id).plan_id == "crece"  # type: ignore[union-attr]
        assert service.usage.open_period().ai_messages_limit == 4000  # type: ignore[union-attr]

    async def test_a_downgrade_waits_and_refuses_what_does_not_fit(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        subscription = _subscribe(db, tenant_id, plan="crece")
        db.add(
            KnowledgeSource(
                tenant_id=tenant_id,
                type="text",
                title="Manual",
                content="x",
                char_count=2_000_000,
                status="indexed",
            )
        )
        db.flush()
        service = BillingService(db, tenant_id, now=T0 + timedelta(days=5))

        with pytest.raises(DowngradeBlocked) as blocked:
            service.change_plan("emprende", "monthly")
        assert "caracteres" in blocked.value.excess[0]

        db.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.tenant_id == tenant_id)
            .values(char_count=10)
        )
        service.change_plan("emprende", "monthly")
        assert (subscription.plan_id, subscription.pending_plan_id) == ("crece", "emprende")

        issue_renewals(db, now=subscription.current_period_end - timedelta(days=3))
        renewal = db.execute(
            select(Charge).where(Charge.tenant_id == tenant_id, Charge.kind == "renewal")
        ).scalar_one()
        assert (renewal.plan_id, renewal.amount_usd) == ("emprende", Decimal("29.00"))
        _pay(
            BillingService(db, tenant_id, now=subscription.current_period_end - timedelta(days=1)),
            renewal,
        )
        assert (subscription.plan_id, subscription.pending_plan_id) == ("emprende", None)

    async def test_an_active_business_changes_plan_instead_of_checking_out(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        _subscribe(db, tenant_id)
        with pytest.raises(UseChangePlan):
            BillingService(db, tenant_id, now=T0).checkout("pro", "monthly")


class TestMessagesPerPeriod:
    async def test_out_of_messages_a_pack_brings_the_agent_back(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        _subscribe(db, tenant_id)
        config = db.get(AgentConfig, tenant_id)
        if config is None:  # registration does not create it; the wizard does
            config = AgentConfig(tenant_id=tenant_id)
            db.add(config)
        config.is_active = True
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.ai_messages_used = 999
        db.flush()

        record_ai_message(db, tenant_id, channel="whatsapp")
        assert not agent_may_reply(db, tenant)
        period = db.execute(
            select(UsagePeriod).where(
                UsagePeriod.tenant_id == tenant_id, UsagePeriod.closed.is_(False)
            )
        ).scalar_one()
        assert period.ai_messages_used == 1000

        service = BillingService(db, tenant_id, now=T0 + timedelta(days=10))
        pack = service.addon_checkout("extra_500_msgs")
        assert pack.amount_usd == Decimal("8.00")
        _pay(service, pack)

        assert period.ai_messages_limit == 1500
        assert agent_may_reply(db, tenant)


class TestTheLedger:
    async def test_it_cannot_be_edited_or_deleted(
        self, db: Session, api: AsyncClient, mail: Any
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        _subscribe(db, tenant_id)

        for statement in (
            "UPDATE billing_ledger SET amount_usd = 0 WHERE tenant_id = :t",
            "DELETE FROM billing_ledger WHERE tenant_id = :t",
        ):
            nested = db.begin_nested()
            with pytest.raises(DBAPIError, match="append-only"):
                db.execute(text(statement), {"t": tenant_id})
            nested.rollback()


class TestRoles:
    async def test_money_is_for_the_owner(self, api: AsyncClient, mail: Any, db: Session) -> None:
        from tests.integration.test_orders_api import operator_for

        _, owner, operator = await operator_for(api, mail, db)

        assert (await api.get(B, headers=operator)).status_code == 403
        assert (await api.get(f"{B}/plans", headers=operator)).status_code == 403
        assert (
            await api.post(f"{B}/checkout", headers=operator, json={"plan_id": "emprende"})
        ).status_code == 403
        plans = (await api.get(f"{B}/plans", headers=owner)).json()
        assert [(p["id"], p["precio_mensual"], p["precio_anual"]) for p in plans["planes"]] == [
            ("emprende", "29.00", "278.40"),
            ("crece", "59.00", "566.40"),
            ("pro", "99.00", "950.40"),
        ]
        assert plans["paquetes"] == [
            {
                "id": "extra_500_msgs",
                "nombre": "500 mensajes extra",
                "mensajes": 500,
                "precio": "8.00",
            }
        ]

    async def test_only_a_platform_admin_records_payments(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner = await _business(api, mail, db)
        charge = (await api.post(f"{B}/checkout", headers=owner, json={"plan_id": "pro"})).json()

        refused = await api.post(
            f"{ADMIN}/payments",
            headers=owner,
            json={"referencia": charge["referencia"], "operacion": "X-1", "monto_usd": "99.00"},
        )

        # The platform panel does not exist for a business: 404, like the rest of it.
        assert refused.status_code == 404
        still = (await api.get(B, headers=owner)).json()["pendientes"]
        assert [c["referencia"] for c in still] == [charge["referencia"]]


def test_prices_live_in_the_table(db: Session) -> None:
    plans = {p.id: p for p in db.execute(select(Plan)).scalars()}
    assert plans["trial"].price_usd_month is None
    assert plans["pro"].price_usd_year == Decimal("950.40")


class TestTheOwnersEndpoints:
    async def test_change_plan_cancel_resume_pack_and_drop_a_charge(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner = await _business(api, mail, db)
        _subscribe(db, tenant_id, now=datetime.now(UTC))

        upgrade = (
            await api.post(f"{B}/change-plan", headers=owner, json={"plan_id": "pro"})
        ).json()
        dropped = await api.post(f"{B}/charges/{upgrade['cobro']['id']}/cancel", headers=owner)
        again = await api.post(f"{B}/charges/{upgrade['cobro']['id']}/cancel", headers=owner)
        same = await api.post(f"{B}/change-plan", headers=owner, json={"plan_id": "emprende"})
        down = (
            await api.post(
                f"{B}/change-plan", headers=owner, json={"plan_id": "emprende", "ciclo": "annual"}
            )
        ).json()
        pack = await api.post(f"{B}/addons/extra_500_msgs/checkout", headers=owner)
        unknown = await api.post(f"{B}/addons/nada/checkout", headers=owner)
        cancel = (await api.post(f"{B}/cancel", headers=owner)).json()
        resume = (await api.post(f"{B}/resume", headers=owner)).json()
        resume_again = await api.post(f"{B}/resume", headers=owner)

        assert upgrade["cobro"]["tipo"] == "upgrade" and Decimal(upgrade["cobro"]["credito"]) > 0
        assert dropped.json()["estado"] == "canceled" and again.status_code == 409
        assert same.status_code == 409 and same.json()["error"] == "same_plan"
        # Emprende annual (278.40 / 12 = 23.20 a month) is a downgrade from 29 a month.
        assert down["programado"]["cambio_programado"] == "Emprende"
        assert pack.status_code == 200 and pack.json()["monto"] == "8.00"
        assert unknown.status_code == 404
        assert cancel["cancela_al_final"] is True and cancel["cambio_programado"] is None
        assert resume["cancela_al_final"] is False and resume_again.status_code == 409

    async def test_without_a_plan_there_is_nothing_to_change_or_cancel(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner = await _business(api, mail, db)

        for path, body in (
            ("/change-plan", {"plan_id": "pro"}),
            ("/cancel", None),
            ("/addons/extra_500_msgs/checkout", None),
        ):
            answer = await api.post(f"{B}{path}", headers=owner, json=body)
            assert answer.status_code == 409, path
        trial = await api.post(f"{B}/checkout", headers=owner, json={"plan_id": "trial"})
        missing = await api.post(f"{B}/checkout", headers=owner, json={"plan_id": "nada"})
        assert trial.status_code == 400 and missing.status_code == 404


class TestTheAdminsEndpoints:
    async def test_list_email_refund_coupon_and_courtesy(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner = await _business(api, mail, db)
        admin, _ = await _admin(api, mail, db)
        charge = (await api.post(f"{B}/checkout", headers=owner, json={"plan_id": "crece"})).json()

        listed = (await api.get(f"{ADMIN}/subscriptions", headers=admin)).json()
        ours = next(r for r in listed if r["tenant_id"] == str(tenant_id))
        assert [c["referencia"] for c in ours["pendientes"]] == [charge["referencia"]]

        nowhere = await api.post(
            f"{ADMIN}/payments",
            headers=admin,
            json={"referencia": "LEO-ZZZZZZ", "operacion": "OP-1", "monto_usd": "59.00"},
        )
        wrong = await api.post(
            f"{ADMIN}/payments",
            headers=admin,
            json={"referencia": charge["referencia"], "operacion": "OP-2", "monto_usd": "58.00"},
        )
        paid = await api.post(
            f"{ADMIN}/payments",
            headers=admin,
            json={
                "referencia": charge["referencia"],
                "operacion": "OP-3",
                "monto_usd": "59.00",
                "monto_local": "221.25",
                "moneda_local": "PEN",
            },
        )
        assert nowhere.status_code == 404
        assert wrong.status_code == 409 and wrong.json()["error"] == "amount_mismatch"
        assert paid.status_code == 201
        assert mail.last.subject == "Recibimos tu pago"
        assert paid.json()["comprobante"] in mail.last.text

        refunded = await api.post(
            f"{ADMIN}/payments/{paid.json()['id']}/refund",
            headers=admin,
            json={"motivo": "cobro duplicado"},
        )
        twice = await api.post(
            f"{ADMIN}/payments/{paid.json()['id']}/refund",
            headers=admin,
            json={"motivo": "cobro duplicado"},
        )
        missing = await api.post(
            f"{ADMIN}/payments/{uuid.uuid4()}/refund", headers=admin, json={"motivo": "no existe"}
        )
        assert refunded.json()["estado"] == "refunded"
        assert twice.status_code == 409 and missing.status_code == 404

        extended = await api.post(
            f"{ADMIN}/tenants/{tenant_id}/extend",
            headers=admin,
            json={"dias": 10, "motivo": "caída del servicio"},
        )
        no_plan = await api.post(
            f"{ADMIN}/tenants/{uuid.uuid4()}/extend",
            headers=admin,
            json={"dias": 10, "motivo": "caída del servicio"},
        )
        assert extended.status_code == 200 and no_plan.status_code == 409
        events = _ledger(db, tenant_id)
        assert events[-2:] == ["reembolso", "cortesia"]

        created = await api.post(
            f"{ADMIN}/coupons", headers=admin, json={"codigo": "dup1", "porcentaje": 5}
        )
        duplicate = await api.post(
            f"{ADMIN}/coupons", headers=admin, json={"codigo": "DUP1", "porcentaje": 5}
        )
        listed_coupons = (await api.get(f"{ADMIN}/coupons", headers=admin)).json()
        assert created.status_code == 201 and duplicate.status_code == 409
        assert "DUP1" in {c["codigo"] for c in listed_coupons}

    async def test_a_past_due_courtesy_brings_it_back(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, _ = await _business(api, mail, db)
        admin, _ = await _admin(api, mail, db)
        subscription = _subscribe(db, tenant_id, now=datetime.now(UTC) - timedelta(days=35))
        close_periods(db, now=datetime.now(UTC))
        assert subscription.status == "past_due"

        await api.post(
            f"{ADMIN}/tenants/{tenant_id}/extend",
            headers=admin,
            json={"dias": 30, "motivo": "acuerdo comercial"},
        )

        db.refresh(subscription)
        assert subscription.status == "active" and subscription.grace_until is None


class TestTheWorker:
    async def test_one_pass_sends_what_the_calendar_produced(
        self, db: Session, api: AsyncClient, mail: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import contextmanager

        from app.workers import billing as worker

        tenant_id, _ = await _business(api, mail, db)
        _subscribe(db, tenant_id, now=datetime.now(UTC) - timedelta(days=27))

        @contextmanager
        def same_session() -> Any:
            yield db

        class Failing:
            sent: list[Any] = []

            async def send(self, message: Any) -> None:
                if not self.sent:
                    self.sent.append(message)
                    raise RuntimeError("smtp down")
                self.sent.append(message)

        monkeypatch.setattr(db, "commit", db.flush)
        sent = await worker.billing_calendar({"session_factory": same_session, "email": Failing()})

        assert sent == 0  # the renewal notice was attempted, the mail server was down
        renewal = db.execute(
            select(Charge).where(Charge.tenant_id == tenant_id, Charge.kind == "renewal")
        ).scalar_one()
        assert renewal.status == "pending"
        assert await worker.billing_calendar({"session_factory": same_session}) == 0
