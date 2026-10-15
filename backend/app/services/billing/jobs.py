"""The billing calendar (Feature 009, TASK-905): renewals, the end of a period, dunning.

Three idempotent passes, each taking `now` (tests drive them with a simulated
clock) and returning the emails to send, so a job can be re-run without sending
anything twice:

* `issue_renewals`: `billing_renewal_notice_days` before a period ends, the
  renewal charge (the next plan if a downgrade is waiting) and its notice.
* `close_periods`: a period that ended unpaid goes `past_due` with
  `billing_grace_days` of grace; one marked for cancellation goes `canceled`.
* `dun`: reminders on days 1, 3 and 5 of the grace, and the pause when it runs out.

All of it respects `billing_enforced()` (decision 5 of the plan): with billing off
nobody is asked to pay or paused, and a period that ends simply rolls over.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.billing import Charge, Subscription
from app.models.tenant import Tenant, TenantUser
from app.models.user import User
from app.providers.email.base import EmailMessage
from app.services.billing.money import money, period_end
from app.services.billing.service import BillingService, price_for
from app.services.emails import (
    build_past_due_email,
    build_paused_unpaid_email,
    build_renewal_email,
)
from app.services.trial import billing_enforced, pause_tenant

logger = logging.getLogger(__name__)

REMINDER_DAYS = (1, 3, 5)


def owner_email(session: Session, tenant_id: uuid.UUID) -> str | None:
    return session.execute(
        select(User.email)
        .join(TenantUser, TenantUser.user_id == User.id)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.role == "owner",
            TenantUser.status == "active",
        )
        .limit(1)
    ).scalar_one_or_none()


def _usd(amount: Decimal) -> str:
    return f"US$ {money(amount)}"


def _panel(settings: Settings) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}/plans"


def _day(moment: datetime) -> str:
    return moment.strftime("%d/%m/%Y")


def issue_renewals(session: Session, *, now: datetime) -> list[EmailMessage]:
    """The renewal charge of every period ending within the notice window."""
    settings = get_settings()
    if not billing_enforced():
        return []
    horizon = now + timedelta(days=settings.billing_renewal_notice_days)
    due = session.execute(
        select(Subscription).where(
            Subscription.status == "active",
            Subscription.cancel_at_period_end.is_(False),
            Subscription.current_period_end <= horizon,
        )
    ).scalars()
    emails: list[EmailMessage] = []
    for subscription in due:
        service = BillingService(session, subscription.tenant_id, now=now)
        start = subscription.current_period_end
        if service.charges.pending_renewal_for(start) is not None:
            continue
        plan_id = subscription.pending_plan_id or subscription.plan_id
        cycle = subscription.pending_cycle or subscription.billing_cycle
        plan = service.plan(plan_id)
        listed = price_for(plan, cycle)
        if listed is None:  # pragma: no cover - a bought plan always has a price
            logger.error("renewal plan has no price", extra={"plan": plan_id})
            continue
        # C-004: a business keeps its price; a lower list price reaches it too.
        same = plan_id == subscription.plan_id and cycle == subscription.billing_cycle
        price = min(listed, subscription.price_locked_usd) if same else listed
        charge = service.issue_charge(
            kind="renewal",
            plan_id=plan_id,
            cycle=cycle,
            list_amount=price,
            subscription=subscription,
            period=(start, period_end(start, cycle)),
            due_at=start,
        )
        email = owner_email(session, subscription.tenant_id)
        tenant = session.get(Tenant, subscription.tenant_id)
        if email and tenant:
            emails.append(
                build_renewal_email(
                    to=email,
                    tenant_name=tenant.name,
                    plan=plan.name,
                    amount=_usd(charge.amount_usd),
                    due=_day(start),
                    reference=charge.reference,
                    instructions=settings.billing_payment_instructions,
                    panel_url=_panel(settings),
                )
            )
    session.flush()
    return emails


def close_periods(session: Session, *, now: datetime) -> list[EmailMessage]:
    """Periods that ended: canceled, past due, or (billing off) rolled over."""
    settings = get_settings()
    ended = session.execute(
        select(Subscription).where(
            Subscription.status == "active", Subscription.current_period_end <= now
        )
    ).scalars()
    emails: list[EmailMessage] = []
    for subscription in ended:
        service = BillingService(session, subscription.tenant_id, now=now)
        tenant = service.tenant(lock=True)
        if subscription.cancel_at_period_end:
            subscription.status = "canceled"
            subscription.canceled_at = now
            service.ledger.append("cancelada", hasta=subscription.current_period_end)
            if billing_enforced():
                pause_tenant(session, tenant, now=now)
            continue
        if not billing_enforced():
            # Billing off: nobody is asked to pay; the period rolls over as it was.
            start = subscription.current_period_end
            subscription.current_period_start = start
            subscription.current_period_end = period_end(start, subscription.billing_cycle)
            plan = service.plan(subscription.plan_id)
            service.ledger.append("periodo_sin_cobro", desde=start)
            service.start_usage_period(start, subscription.current_period_end, plan.max_ai_messages)
            continue
        subscription.status = "past_due"
        subscription.grace_until = subscription.current_period_end + timedelta(
            days=settings.billing_grace_days
        )
        subscription.reminders_sent = []
        service.ledger.append("vencida", gracia_hasta=subscription.grace_until)
    session.flush()
    return emails


def dun(session: Session, *, now: datetime) -> list[EmailMessage]:
    """Reminders during the grace (days 1, 3, 5) and the pause when it runs out (HU-02)."""
    settings = get_settings()
    if not billing_enforced():
        return []
    late = session.execute(select(Subscription).where(Subscription.status == "past_due")).scalars()
    emails: list[EmailMessage] = []
    for subscription in late:
        service = BillingService(session, subscription.tenant_id, now=now)
        tenant = service.tenant(lock=True)
        charge = _renewal_owed(session, subscription)
        email = owner_email(session, subscription.tenant_id)
        amount = _usd(charge.amount_usd) if charge else "tu plan"
        reference = charge.reference if charge else ""
        grace = subscription.grace_until or now

        if now >= grace:
            subscription.status = "paused"
            service.ledger.append("pausada_por_falta_de_pago")
            pause_tenant(session, tenant, now=now)
            if email:
                emails.append(
                    build_paused_unpaid_email(
                        to=email,
                        tenant_name=tenant.name,
                        amount=amount,
                        reference=reference,
                        instructions=settings.billing_payment_instructions,
                        panel_url=_panel(settings),
                    )
                )
            continue

        day = (now - subscription.current_period_end).days
        sent = list(subscription.reminders_sent or [])
        due_days = [d for d in REMINDER_DAYS if d <= day and d not in sent]
        if not due_days:
            continue
        subscription.reminders_sent = sorted({*sent, *due_days})
        service.ledger.append("recordatorio", dia=max(due_days))
        if email:
            emails.append(
                build_past_due_email(
                    to=email,
                    tenant_name=tenant.name,
                    amount=amount,
                    grace_until=_day(grace),
                    reference=reference,
                    instructions=settings.billing_payment_instructions,
                    panel_url=_panel(settings),
                )
            )
    session.flush()
    return emails


def _renewal_owed(session: Session, subscription: Subscription) -> Charge | None:
    return session.execute(
        select(Charge)
        .where(
            Charge.tenant_id == subscription.tenant_id,
            Charge.kind == "renewal",
            Charge.status == "pending",
        )
        .order_by(Charge.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
