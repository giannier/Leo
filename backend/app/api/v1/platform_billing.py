"""The platform's side of billing (Feature 009, HU-05): what Giannier does by hand.

    GET  /platform-admin/billing/subscriptions        every business, its plan and what it owes
    POST /platform-admin/billing/payments             record a transfer or Yape against a reference
    POST /platform-admin/billing/payments/{id}/refund mark a payment refunded (money moves outside)
    GET  /platform-admin/billing/coupons | POST       coupons (percentage, expiry, uses)
    POST /platform-admin/billing/tenants/{id}/extend  a courtesy: more days in the current period

Every write is audited with the admin and the reason (`audit_logs`), and lands
on the business's ledger. Recording a payment is idempotent by the operation
number: typing the same transfer twice charges nothing twice.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import EmailProviderDep, PlatformAdminDep, get_db
from app.api.errors import ApiError, error_responses
from app.api.v1.billing import ChargeOut, billing_error, charge_out, concept
from app.core.config import get_settings
from app.models.billing import Charge, Coupon, Payment, Subscription
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.providers.payments import MANUAL
from app.services.admin import record_audit
from app.services.billing.jobs import owner_email
from app.services.billing.money import money
from app.services.billing.service import BillingError, BillingService, tenant_of_reference
from app.services.emails import build_payment_received_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/platform-admin/billing", tags=["platform-admin"])
SessionDep = Annotated[Session, Depends(get_db)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class SubscriptionRow(BaseModel):
    tenant_id: uuid.UUID
    negocio: str
    estado_negocio: str
    plan: str
    estado: str | None
    hasta: datetime | None
    pendientes: list[ChargeOut]


class ManualPaymentBody(BaseModel):
    referencia: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=6, max_length=20)
    ]
    # The bank's operation number or the Yape code: what makes a replay harmless.
    operacion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=80)]
    monto_usd: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    monto_local: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    moneda_local: Annotated[str, StringConstraints(pattern="^[A-Z]{3}$")] | None = None
    nota: Annotated[str, StringConstraints(max_length=300)] | None = None


class PaymentRow(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    comprobante: str
    concepto: str
    monto: Decimal
    estado: str
    repetido: bool = False


class RefundBody(BaseModel):
    motivo: Reason


class CouponBody(BaseModel):
    codigo: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern="^[A-Za-z0-9_-]{3,40}$")
    ]
    porcentaje: int = Field(ge=1, le=100)
    vence: datetime | None = None
    usos_maximos: int | None = Field(default=None, ge=1)


class CouponRow(BaseModel):
    codigo: str
    porcentaje: int
    vence: datetime | None
    usos_maximos: int | None


class ExtendBody(BaseModel):
    dias: int = Field(ge=1, le=90)
    motivo: Reason


@router.get("/subscriptions", response_model=list[SubscriptionRow])
async def subscriptions(session: SessionDep, admin: PlatformAdminDep) -> list[SubscriptionRow]:
    rows = session.execute(
        select(Tenant, Subscription)
        .outerjoin(Subscription, Subscription.tenant_id == Tenant.id)
        .where(Tenant.status != "archived")
        .order_by(Tenant.created_at.desc())
        .limit(500)
    ).all()
    out = []
    for tenant, subscription in rows:
        pending = session.execute(
            select(Charge).where(Charge.tenant_id == tenant.id, Charge.status == "pending")
        ).scalars()
        out.append(
            SubscriptionRow(
                tenant_id=tenant.id,
                negocio=tenant.name,
                estado_negocio=tenant.status,
                plan=tenant.plan_id,
                estado=subscription.status if subscription else None,
                hasta=subscription.current_period_end if subscription else None,
                pendientes=[charge_out(session, c) for c in pending],
            )
        )
    return out


@router.post(
    "/payments",
    response_model=PaymentRow,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        (404, "`not_found`: no charge with that reference"),
        (409, "`charge_not_pending` or `amount_mismatch`"),
    ),
)
async def record_payment(
    body: ManualPaymentBody, session: SessionDep, admin: PlatformAdminDep, email: EmailProviderDep
) -> PaymentRow:
    """A transfer or Yape arrived: the same effect a gateway's webhook would have (RF-007)."""
    reference = body.referencia.upper()
    tenant_id = tenant_of_reference(session, reference)
    if tenant_id is None:
        raise ApiError(
            status_code=404, error="not_found", message="No hay un cobro con esa referencia."
        )
    service = BillingService(session, tenant_id, now=datetime.now(UTC))
    try:
        applied = service.apply_payment(
            reference=reference,
            provider=MANUAL,
            provider_payment_id=f"{MANUAL}:{body.operacion}",
            amount_usd=body.monto_usd,
            amount_local=body.monto_local,
            currency_local=body.moneda_local,
            registered_by=admin.id,
            note=body.nota,
        )
    except BillingError as exc:
        raise billing_error(exc) from exc
    if not applied.replayed:
        record_audit(
            session,
            actor_id=admin.id,
            tenant_id=tenant_id,
            action="billing.payment_recorded",
            detail={
                "referencia": reference,
                "operacion": body.operacion,
                "monto_usd": str(money(body.monto_usd)),
                "comprobante": applied.payment.receipt_number,
            },
        )
    session.commit()

    if not applied.replayed and email is not None:
        owner = owner_email(session, tenant_id)
        tenant = session.get(Tenant, tenant_id)
        if owner and tenant:
            settings = get_settings()
            try:
                await email.send(
                    build_payment_received_email(
                        to=owner,
                        tenant_name=tenant.name,
                        amount=f"US$ {applied.payment.amount_usd}",
                        concept=concept(session, applied.charge),
                        receipt=applied.payment.receipt_number,
                        panel_url=f"{settings.frontend_base_url.rstrip('/')}/plans",
                    )
                )
            except Exception:  # noqa: BLE001 - the payment is recorded; the email is a courtesy
                logger.exception("payment received email failed")
    return PaymentRow(
        id=applied.payment.id,
        tenant_id=tenant_id,
        comprobante=applied.payment.receipt_number,
        concepto=concept(session, applied.charge),
        monto=applied.payment.amount_usd,
        estado=applied.payment.status,
        repetido=applied.replayed,
    )


@router.post(
    "/payments/{payment_id}/refund",
    response_model=PaymentRow,
    responses=error_responses((404, "`not_found`"), (409, "`already_refunded`")),
)
async def refund(
    payment_id: uuid.UUID, body: RefundBody, session: SessionDep, admin: PlatformAdminDep
) -> PaymentRow:
    """Recorded only: the money goes back outside Leo (manual refund, RF-001)."""
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise ApiError(status_code=404, error="not_found", message="No existe ese pago.")
    if payment.status == "refunded":
        raise ApiError(status_code=409, error="already_refunded", message="Ya está reembolsado.")
    payment.status = "refunded"
    service = BillingService(session, payment.tenant_id, now=datetime.now(UTC))
    service.ledger.append(
        "reembolso",
        amount_usd=-payment.amount_usd,
        comprobante=payment.receipt_number,
        motivo=body.motivo,
    )
    record_audit(
        session,
        actor_id=admin.id,
        tenant_id=payment.tenant_id,
        action="billing.payment_refunded",
        detail={"comprobante": payment.receipt_number, "motivo": body.motivo},
    )
    session.commit()
    charge = session.get(Charge, payment.charge_id)
    return PaymentRow(
        id=payment.id,
        tenant_id=payment.tenant_id,
        comprobante=payment.receipt_number,
        concepto=concept(session, charge) if charge else payment.kind,
        monto=payment.amount_usd,
        estado=payment.status,
    )


@router.get("/coupons", response_model=list[CouponRow])
async def coupons(session: SessionDep, admin: PlatformAdminDep) -> list[CouponRow]:
    rows = session.execute(select(Coupon).order_by(Coupon.created_at.desc())).scalars()
    return [
        CouponRow(
            codigo=c.code,
            porcentaje=c.percent_off,
            vence=c.expires_at,
            usos_maximos=c.max_redemptions,
        )
        for c in rows
    ]


@router.post(
    "/coupons",
    response_model=CouponRow,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses((409, "`coupon_exists`")),
)
async def create_coupon(
    body: CouponBody, session: SessionDep, admin: PlatformAdminDep
) -> CouponRow:
    code = body.codigo.upper()
    if session.get(Coupon, code) is not None:
        raise ApiError(status_code=409, error="coupon_exists", message="Ese cupón ya existe.")
    session.add(
        Coupon(
            code=code,
            percent_off=body.porcentaje,
            expires_at=body.vence,
            max_redemptions=body.usos_maximos,
            created_by=admin.id,
        )
    )
    record_audit(
        session,
        actor_id=admin.id,
        action="billing.coupon_created",
        detail={"codigo": code, "porcentaje": body.porcentaje},
    )
    session.commit()
    return CouponRow(
        codigo=code, porcentaje=body.porcentaje, vence=body.vence, usos_maximos=body.usos_maximos
    )


@router.post(
    "/tenants/{tenant_id}/extend",
    responses=error_responses((404, "`not_found`"), (409, "`no_active_subscription`")),
)
async def extend(
    tenant_id: uuid.UUID, body: ExtendBody, session: SessionDep, admin: PlatformAdminDep
) -> dict[str, str]:
    """A courtesy: the current period, and its message period, last `dias` longer."""
    service = BillingService(session, tenant_id, now=datetime.now(UTC))
    subscription = service.subscriptions.current(lock=True)
    if subscription is None or subscription.status not in ("active", "past_due"):
        raise ApiError(
            status_code=409,
            error="no_active_subscription",
            message="No tiene una suscripción vigente.",
        )
    previous = subscription.current_period_end
    subscription.current_period_end = previous + timedelta(days=body.dias)
    if subscription.status == "past_due":
        subscription.status = "active"
        subscription.grace_until = None
    period = service.usage.open_period(lock=True)
    if period is not None:
        period.period_end = subscription.current_period_end
    service.ledger.append(
        "cortesia", dias=body.dias, motivo=body.motivo, hasta=subscription.current_period_end
    )
    record_audit(
        session,
        actor_id=admin.id,
        tenant_id=tenant_id,
        action="billing.period_extended",
        detail={"dias": body.dias, "motivo": body.motivo},
    )
    session.commit()
    plan = session.get(Plan, subscription.plan_id)
    return {
        "plan": plan.name if plan else subscription.plan_id,
        "hasta": subscription.current_period_end.isoformat(),
    }
