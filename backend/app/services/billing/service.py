"""What a business owes the platform, and what happens when it pays (Feature 009).

The subscription is a small state machine (RF-003), kept in step with
`tenants.status`:

    trialing ──pago──► active ──fin del período sin pago──► past_due ──gracia──► paused
                         ▲  ▲                                   │                  │
                         │  └─────────────── pago ──────────────┘                  │
                         └───────────────────────── pago ──────────────────────────┘
    active ──cancelación al fin del período──► canceled (el negocio queda en pausa)

v1 is paid by hand (Giannier, 2026-10-14): every purchase is a `Charge` with a
reference; an admin records the transfer or Yape against it and `apply_payment`
does what a gateway's webhook would. It is idempotent by the provider's payment
id, so recording the same operation twice changes nothing.

Every method takes `now`: the jobs and their tests agree on one clock. Money is
`Decimal` from the table to the receipt (Constitution); rounding favours the
customer (`money.py`). Every money or state movement appends to the ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.billing import Charge, Coupon, CouponRedemption, Payment, PlanAddon, Subscription
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.repositories.billing import (
    ChargeRepository,
    LedgerRepository,
    PaymentRepository,
    SubscriptionRepository,
    UsagePeriodRepository,
)
from app.repositories.knowledge import KnowledgeSourceRepository
from app.services.billing.money import (
    ZERO,
    charge_amount,
    money,
    new_reference,
    percent_off,
    period_end,
    remaining_days,
    unused_credit,
)
from app.services.team import TeamService

CYCLES = ("monthly", "annual")
# A checkout or upgrade charge waits this long before it lapses (renewals wait their grace).
CHARGE_VALID_DAYS = 7


class BillingError(Exception):
    """Base of what a billing request can be refused for; `code` is the API's error."""

    code = "billing_error"


class UnknownPlan(BillingError):
    code = "unknown_plan"


class NotPurchasable(BillingError):
    """The plan has no price for that cycle (the trial)."""

    code = "plan_not_purchasable"


class UseChangePlan(BillingError):
    """An active subscription changes plan; it does not check out again."""

    code = "use_change_plan"


class NoActiveSubscription(BillingError):
    code = "no_active_subscription"


class SamePlan(BillingError):
    code = "same_plan"


class CouponInvalid(BillingError):
    code = "coupon_invalid"


class ChargeNotFound(BillingError):
    code = "not_found"


class ChargeNotPending(BillingError):
    code = "charge_not_pending"


class AmountMismatch(BillingError):
    """A manual payment must match the charge to the cent."""

    code = "amount_mismatch"


class DowngradeBlocked(BillingError):
    """The smaller plan cannot hold what the business already uses (HU-03)."""

    code = "downgrade_blocked"

    def __init__(self, excess: list[str]) -> None:
        super().__init__("; ".join(excess))
        self.excess = excess


@dataclass(frozen=True)
class AppliedPayment:
    payment: Payment
    charge: Charge
    replayed: bool = False


def price_for(plan: Plan, cycle: str) -> Decimal | None:
    if cycle not in CYCLES:
        raise BillingError(f"unknown billing cycle {cycle!r}")
    value = plan.price_usd_month if cycle == "monthly" else plan.price_usd_year
    return money(value) if value is not None else None


def monthly_equivalent(plan: Plan, cycle: str) -> Decimal:
    """For comparing plans across cycles: what a month costs."""
    price = price_for(plan, cycle) or ZERO
    return price if cycle == "monthly" else price / 12


def tenant_of_reference(session: Session, reference: str) -> uuid.UUID | None:
    """Which business a payment reference belongs to (the admin types the reference)."""
    return session.execute(
        select(Charge.tenant_id).where(Charge.reference == reference.strip().upper())
    ).scalar_one_or_none()


class BillingService:
    def __init__(self, session: Session, tenant_id: uuid.UUID, *, now: datetime) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._now = now
        self.subscriptions = SubscriptionRepository(session, tenant_id)
        self.charges = ChargeRepository(session, tenant_id)
        self.payments = PaymentRepository(session, tenant_id)
        self.usage = UsagePeriodRepository(session, tenant_id)
        self.ledger = LedgerRepository(session, tenant_id)

    # ---------------------------------------------------------------- reads

    def tenant(self, *, lock: bool = False) -> Tenant:
        statement = select(Tenant).where(Tenant.id == self._tenant_id)
        if lock:
            statement = statement.with_for_update()
        tenant = self._session.execute(statement).scalar_one_or_none()
        if tenant is None:  # pragma: no cover - callers are authenticated
            raise BillingError("tenant not found")
        return tenant

    def plan(self, plan_id: str) -> Plan:
        plan = self._session.get(Plan, plan_id)
        if plan is None or not plan.is_active:
            raise UnknownPlan(plan_id)
        return plan

    # ------------------------------------------------------------ purchases

    def checkout(self, plan_id: str, cycle: str, *, coupon: str | None = None) -> Charge:
        """A first subscription, or one after a pause or a cancellation (HU-01)."""
        plan = self.plan(plan_id)
        price = price_for(plan, cycle)
        if price is None:
            raise NotPurchasable(plan_id)
        current = self.subscriptions.current()
        if current is not None and current.status in ("active", "past_due"):
            raise UseChangePlan(current.plan_id)
        discount = self._discount(coupon, price)
        # One open checkout at a time: paying an old one by mistake would be a double charge.
        for charge in self.charges.pending():
            if charge.kind == "subscription":
                self._void(charge, "reemplazado")
        return self.issue_charge(
            kind="subscription",
            plan_id=plan.id,
            cycle=cycle,
            list_amount=price,
            discount=discount,
            coupon=coupon,
            subscription=current,
        )

    def change_plan(self, plan_id: str, cycle: str) -> Charge | Subscription:
        """Upgrade now with credit for the unused days; downgrade at the period's end (HU-03)."""
        subscription = self.subscriptions.current(lock=True)
        if subscription is None or subscription.status != "active":
            raise NoActiveSubscription()
        target = self.plan(plan_id)
        price = price_for(target, cycle)
        if price is None:
            raise NotPurchasable(plan_id)
        current = self.plan(subscription.plan_id)
        if target.id == current.id and cycle == subscription.billing_cycle:
            raise SamePlan(plan_id)

        if monthly_equivalent(target, cycle) > monthly_equivalent(
            current, subscription.billing_cycle
        ):
            for charge in self.charges.pending():
                if charge.kind == "upgrade":
                    self._void(charge, "reemplazado")
            credit = unused_credit(
                subscription.price_locked_usd,
                subscription.current_period_start,
                subscription.current_period_end,
                self._now,
            )
            return self.issue_charge(
                kind="upgrade",
                plan_id=target.id,
                cycle=cycle,
                list_amount=price,
                credit=credit,
                subscription=subscription,
            )

        excess = self.excess_for(target)
        if excess:
            raise DowngradeBlocked(excess)
        subscription.pending_plan_id = target.id
        subscription.pending_cycle = cycle
        self.ledger.append(
            "downgrade_programado",
            plan=target.id,
            cycle=cycle,
            efectivo=subscription.current_period_end,
        )
        self._session.flush()
        return subscription

    def excess_for(self, plan: Plan) -> list[str]:
        """What the business uses beyond `plan`'s limits, in words (HU-03)."""
        excess: list[str] = []
        seats = TeamService(self._session, self._tenant_id).seat_usage().used
        if seats > plan.max_seats:
            excess.append(
                f"Tienes {seats} asientos (miembros e invitaciones) y {plan.name} permite "
                f"{plan.max_seats}: quita {seats - plan.max_seats}."
            )
        sources = KnowledgeSourceRepository(self._session, self._tenant_id)
        chars = sources.total_chars()
        if chars > plan.max_rag_chars:
            excess.append(
                f"Tu conocimiento ocupa {chars:,} caracteres y {plan.name} permite "
                f"{plan.max_rag_chars:,}."
            )
        files = sources.active_file_count()
        if files > plan.max_knowledge_files:
            excess.append(
                f"Tienes {files} fuentes de conocimiento y {plan.name} permite "
                f"{plan.max_knowledge_files}."
            )
        return excess

    def cancel(self) -> Subscription:
        """At the end of the paid period, never before (HU-03)."""
        subscription = self.subscriptions.current(lock=True)
        if subscription is None or subscription.status not in ("active", "past_due"):
            raise NoActiveSubscription()
        subscription.cancel_at_period_end = True
        subscription.pending_plan_id = None
        subscription.pending_cycle = None
        for charge in self.charges.pending():
            if charge.kind == "renewal":
                self._void(charge, "cancelación")
        self.ledger.append("cancelacion_programada", efectiva=subscription.current_period_end)
        self._session.flush()
        return subscription

    def resume(self) -> Subscription:
        subscription = self.subscriptions.current(lock=True)
        if subscription is None or not subscription.cancel_at_period_end:
            raise NoActiveSubscription()
        subscription.cancel_at_period_end = False
        self.ledger.append("cancelacion_revertida")
        self._session.flush()
        return subscription

    def addon_checkout(self, addon_id: str) -> Charge:
        """A pack of extra messages for the current period (HU-04, RF-005)."""
        addon = self._session.get(PlanAddon, addon_id)
        if addon is None or not addon.is_active:
            raise UnknownPlan(addon_id)
        subscription = self.subscriptions.current()
        if subscription is None or subscription.status not in ("active", "past_due"):
            raise NoActiveSubscription()
        return self.issue_charge(
            kind="addon",
            addon_id=addon.id,
            list_amount=money(addon.price_usd),
            subscription=subscription,
        )

    def cancel_charge(self, charge_id: uuid.UUID) -> Charge:
        charge = self.charges.get(charge_id)
        if charge is None:
            raise ChargeNotFound(str(charge_id))
        if charge.status != "pending":
            raise ChargeNotPending(charge.reference)
        self._void(charge, "anulado por el negocio")
        self._session.flush()
        return charge

    # ------------------------------------------------------------- payments

    def apply_payment(
        self,
        *,
        reference: str,
        provider: str,
        provider_payment_id: str,
        amount_usd: Decimal,
        amount_local: Decimal | None = None,
        currency_local: str | None = None,
        registered_by: uuid.UUID | None = None,
        note: str | None = None,
    ) -> AppliedPayment:
        """Money arrived for a charge: apply it, once (RF-002, RF-007)."""
        replay = self._session.execute(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        ).scalar_one_or_none()
        if replay is not None:
            charge = self._session.get(Charge, replay.charge_id)
            assert charge is not None
            if replay.tenant_id != self._tenant_id:
                raise ChargeNotFound(reference)
            return AppliedPayment(replay, charge, replayed=True)

        tenant = self.tenant(lock=True)
        charge = self.charges.by_reference(reference.strip().upper(), lock=True)
        if charge is None:
            raise ChargeNotFound(reference)
        if charge.status != "pending":
            raise ChargeNotPending(charge.reference)
        if money(amount_usd) != charge.amount_usd:
            raise AmountMismatch(f"{money(amount_usd)} != {charge.amount_usd}")

        receipt = self._session.execute(text("SELECT nextval('receipt_number_seq')")).scalar_one()
        payment = Payment(
            tenant_id=self._tenant_id,
            subscription_id=charge.subscription_id,
            charge_id=charge.id,
            provider=provider,
            provider_payment_id=provider_payment_id,
            kind=charge.kind,
            amount_usd=charge.amount_usd,
            amount_local=money(amount_local) if amount_local is not None else None,
            currency_local=currency_local,
            status="approved",
            receipt_number=f"LEO-{int(receipt):06d}",
            registered_by=registered_by,
            note=note,
        )
        self._session.add(payment)
        charge.status = "paid"
        charge.paid_at = self._now
        if charge.coupon_code:
            self._session.add(CouponRedemption(coupon_code=charge.coupon_code, tenant_id=tenant.id))
        self.ledger.append(
            "pago",
            amount_usd=charge.amount_usd,
            referencia=charge.reference,
            tipo=charge.kind,
            proveedor=provider,
            comprobante=payment.receipt_number,
        )

        if charge.kind == "addon":
            self._apply_addon(charge)
        else:
            self._apply_plan(tenant, charge)
        self._session.flush()
        payment.subscription_id = payment.subscription_id or charge.subscription_id
        return AppliedPayment(payment, charge)

    # ----------------------------------------------------------- internals

    def _apply_plan(self, tenant: Tenant, charge: Charge) -> None:
        assert charge.plan_id is not None and charge.billing_cycle is not None
        plan = self.plan(charge.plan_id)
        subscription = self.subscriptions.current(lock=True)

        start = self._now
        # In grace the calendar goes on; paused or canceled, a new period starts today.
        if (
            charge.kind == "renewal"
            and subscription is not None
            and subscription.status in ("active", "past_due")
            and charge.period_start
        ):
            start = charge.period_start
        end = period_end(start, charge.billing_cycle)
        if charge.kind == "subscription" and tenant.status == "trial":
            end += remaining_days(tenant.trial_ends_at, self._now)  # C-005

        if subscription is None:
            subscription = Subscription(
                tenant_id=tenant.id,
                plan_id=plan.id,
                status="active",
                billing_cycle=charge.billing_cycle,
                price_locked_usd=charge.list_amount_usd,
                current_period_start=start,
                current_period_end=end,
            )
            self._session.add(subscription)
            self._session.flush()
        previous = subscription.status
        subscription.plan_id = plan.id
        subscription.billing_cycle = charge.billing_cycle
        subscription.price_locked_usd = charge.list_amount_usd
        subscription.status = "active"
        subscription.current_period_start = start
        subscription.current_period_end = end
        subscription.pending_plan_id = None
        subscription.pending_cycle = None
        subscription.grace_until = None
        subscription.cancel_at_period_end = False
        subscription.canceled_at = None
        subscription.reminders_sent = []
        charge.subscription_id = subscription.id

        tenant.plan_id = plan.id
        tenant.status = "active"
        tenant.paused_at = None
        self.ledger.append(
            "periodo",
            plan=plan.id,
            cycle=charge.billing_cycle,
            desde=start,
            hasta=end,
            estado_anterior=previous,
        )
        self.start_usage_period(start, end, plan.max_ai_messages)

    def start_usage_period(self, start: datetime, end: datetime, limit: int) -> None:
        """Close the running period and open the next; the counter starts at zero (RF-004)."""
        current = self.usage.open_period(lock=True)
        tenant = self.tenant(lock=True)
        if current is not None and current.period_start == start:
            # The same start (an upgrade paid twice in one instant): extend it, keep the count.
            current.period_end = end
            current.ai_messages_limit = limit
            return
        if current is not None:
            current.closed = True
            current.ai_messages_used = max(current.ai_messages_used, tenant.ai_messages_used)
        self.usage.add(period_start=start, period_end=end, ai_messages_limit=limit)
        tenant.ai_messages_used = 0

    def _apply_addon(self, charge: Charge) -> None:
        addon = self._session.get(PlanAddon, charge.addon_id)
        assert addon is not None
        period = self.usage.open_period(lock=True)
        if period is None:  # pragma: no cover - an active subscription always has one
            raise NoActiveSubscription()
        period.ai_messages_limit += addon.ai_messages
        self.ledger.append("paquete", paquete=addon.id, mensajes=addon.ai_messages)

    def issue_charge(
        self,
        *,
        kind: str,
        list_amount: Decimal,
        plan_id: str | None = None,
        cycle: str | None = None,
        addon_id: str | None = None,
        discount: Decimal = ZERO,
        credit: Decimal = ZERO,
        coupon: str | None = None,
        subscription: Subscription | None = None,
        period: tuple[datetime, datetime] | None = None,
        due_at: datetime | None = None,
    ) -> Charge:
        amount = charge_amount(list_amount, discount=discount, credit=credit)
        charge = self.charges.add(
            subscription_id=subscription.id if subscription else None,
            kind=kind,
            plan_id=plan_id,
            addon_id=addon_id,
            billing_cycle=cycle,
            list_amount_usd=money(list_amount),
            discount_usd=discount,
            credit_usd=min(credit, money(list_amount) - discount),
            amount_usd=amount,
            coupon_code=coupon.strip().upper() if coupon else None,
            reference=self._unique_reference(),
            period_start=period[0] if period else None,
            period_end=period[1] if period else None,
            due_at=due_at or self._now + timedelta(days=CHARGE_VALID_DAYS),
        )
        self.ledger.append(
            "cobro",
            amount_usd=amount,
            referencia=charge.reference,
            tipo=kind,
            plan=plan_id,
            paquete=addon_id,
            lista=money(list_amount),
            descuento=discount,
            credito=credit,
        )
        if amount == ZERO:
            # Nothing to pay (a 100 % coupon): applied now, no payment row, on the ledger.
            self._settle_free(charge)
        return charge

    def _settle_free(self, charge: Charge) -> None:
        tenant = self.tenant(lock=True)
        charge.status = "paid"
        charge.paid_at = self._now
        if charge.coupon_code:
            self._session.add(CouponRedemption(coupon_code=charge.coupon_code, tenant_id=tenant.id))
        self.ledger.append("cobro_sin_monto", referencia=charge.reference, tipo=charge.kind)
        if charge.kind == "addon":
            self._apply_addon(charge)
        else:
            self._apply_plan(tenant, charge)
        self._session.flush()

    def _void(self, charge: Charge, why: str) -> None:
        charge.status = "canceled"
        self.ledger.append("cobro_anulado", referencia=charge.reference, motivo=why)

    def _discount(self, code: str | None, price: Decimal) -> Decimal:
        """A coupon's discount, or why it does not apply (RF-006)."""
        if not code:
            return ZERO
        coupon = self._session.get(Coupon, code.strip().upper())
        if coupon is None:
            raise CouponInvalid("Ese cupón no existe.")
        if coupon.expires_at is not None and coupon.expires_at <= self._now:
            raise CouponInvalid("Ese cupón venció.")
        used = self._session.get(CouponRedemption, (coupon.code, self._tenant_id))
        if used is not None:
            raise CouponInvalid("Ya usaste ese cupón.")
        if coupon.max_redemptions is not None:
            redeemed = self._session.execute(
                select(func.count())
                .select_from(CouponRedemption)
                .where(CouponRedemption.coupon_code == coupon.code)
            ).scalar_one()
            if redeemed >= coupon.max_redemptions:
                raise CouponInvalid("Ese cupón ya no tiene usos disponibles.")
        return percent_off(price, coupon.percent_off)

    def _unique_reference(self) -> str:
        while True:
            reference = new_reference()
            exists = self._session.execute(
                select(Charge.id).where(Charge.reference == reference)
            ).first()
            if exists is None:
                return reference
