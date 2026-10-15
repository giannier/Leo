"""The business's side of billing (Feature 009, TASK-908 API).

    GET  /tenant/billing/plans                 plans, prices and packs (from the tables)
    GET  /tenant/billing                       subscription, usage, what is owed, payments
    POST /tenant/billing/checkout              a plan: a charge with its reference
    POST /tenant/billing/change-plan           upgrade (charge with credit) or scheduled downgrade
    POST /tenant/billing/cancel | resume       at the end of the period / undo it
    POST /tenant/billing/addons/{id}/checkout  a pack of extra messages
    POST /tenant/billing/charges/{id}/cancel   drop a charge not paid yet
    GET  /tenant/billing/payments/{id}/receipt.pdf

Money is shown to the owner and the admins; an operator works the inbox and does
not see it (010 RF-004). Buying, changing and cancelling is the owner's alone:
it commits the account to paying.

v1 is paid by hand (Giannier, 2026-10-14): a charge comes back with its reference
and the platform's instructions; nothing here moves money.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AccountOwnerDep, ConfigurationManagerDep, get_db
from app.api.errors import ApiError, error_responses
from app.core.config import get_settings
from app.models.billing import Charge, PlanAddon, Subscription
from app.models.plan import Plan
from app.providers.payments import ManualPaymentProvider
from app.services.billing.money import money
from app.services.billing.receipt import ReceiptData, render_receipt
from app.services.billing.service import (
    BillingError,
    BillingService,
    ChargeNotFound,
    DowngradeBlocked,
    price_for,
)
from app.services.trial import billing_enforced, message_limit

router = APIRouter(prefix="/tenant/billing", tags=["billing"])
SessionDep = Annotated[Session, Depends(get_db)]
Cycle = Literal["monthly", "annual"]

STATUS_BY_CODE = {
    "unknown_plan": 404,
    "not_found": 404,
    "plan_not_purchasable": 400,
    "coupon_invalid": 400,
    "use_change_plan": 409,
    "no_active_subscription": 409,
    "same_plan": 409,
    "charge_not_pending": 409,
    "downgrade_blocked": 409,
    "amount_mismatch": 409,
}
KIND_LABELS = {
    "subscription": "Plan",
    "renewal": "Renovación",
    "upgrade": "Cambio de plan",
    "addon": "Paquete",
}


# ------------------------------------------------------------------ schemas


class PlanOut(BaseModel):
    id: str
    nombre: str
    precio_mensual: Decimal | None
    precio_anual: Decimal | None
    asientos: int
    mensajes: int
    caracteres_conocimiento: int
    fuentes_conocimiento: int
    niveles: list[str]


class AddonOut(BaseModel):
    id: str
    nombre: str
    mensajes: int
    precio: Decimal


class Catalog(BaseModel):
    planes: list[PlanOut]
    paquetes: list[AddonOut]
    soles_por_dolar: Decimal | None
    instrucciones_pago: str


class ChargeOut(BaseModel):
    id: uuid.UUID
    referencia: str
    tipo: str
    concepto: str
    lista: Decimal
    descuento: Decimal
    credito: Decimal
    monto: Decimal
    cupon: str | None
    estado: str
    vence: datetime | None
    instrucciones: str


class SubscriptionOut(BaseModel):
    plan: str
    plan_nombre: str
    estado: str
    ciclo: str
    precio: Decimal
    desde: datetime
    hasta: datetime
    gracia_hasta: datetime | None
    cancela_al_final: bool
    cambio_programado: str | None


class UsageOut(BaseModel):
    usados: int
    limite: int
    desde: datetime | None
    hasta: datetime | None


class PaymentOut(BaseModel):
    id: uuid.UUID
    comprobante: str
    concepto: str
    monto: Decimal
    estado: str
    en: datetime


class BillingOut(BaseModel):
    estado_negocio: str
    prueba_hasta: datetime | None
    suscripcion: SubscriptionOut | None
    consumo: UsageOut
    pendientes: list[ChargeOut]
    pagos: list[PaymentOut]
    cobro_activo: bool


class CheckoutBody(BaseModel):
    plan_id: str
    ciclo: Cycle = "monthly"
    cupon: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None


class ChangePlanBody(BaseModel):
    plan_id: str
    ciclo: Cycle = "monthly"


class ChangeResult(BaseModel):
    cobro: ChargeOut | None = None
    programado: SubscriptionOut | None = None


# ------------------------------------------------------------------ helpers


def _service(session: Session, tenant_id: uuid.UUID) -> BillingService:
    return BillingService(session, tenant_id, now=datetime.now(UTC))


def billing_error(exc: BillingError) -> ApiError:
    fields = {}
    if isinstance(exc, DowngradeBlocked):
        fields = {f"exceso_{i}": line for i, line in enumerate(exc.excess)}
    message = str(exc) or exc.code
    if isinstance(exc, ChargeNotFound):
        message = "No encontramos ese cobro."
    return ApiError(
        status_code=STATUS_BY_CODE.get(exc.code, 400),
        error=exc.code,
        message=message,
        fields=fields,
    )


def concept(session: Session, charge: Charge) -> str:
    label = KIND_LABELS.get(charge.kind, charge.kind)
    if charge.addon_id:
        addon = session.get(PlanAddon, charge.addon_id)
        return f"{label}: {addon.name if addon else charge.addon_id}"
    plan = session.get(Plan, charge.plan_id) if charge.plan_id else None
    cycle = {"monthly": "mensual", "annual": "anual"}.get(charge.billing_cycle or "", "")
    return f"{label}: {plan.name if plan else charge.plan_id} {cycle}".strip()


def charge_out(session: Session, charge: Charge) -> ChargeOut:
    checkout = ManualPaymentProvider(get_settings().billing_payment_instructions).checkout(
        reference=charge.reference, amount_usd=charge.amount_usd, concept=concept(session, charge)
    )
    return ChargeOut(
        id=charge.id,
        referencia=charge.reference,
        tipo=charge.kind,
        concepto=concept(session, charge),
        lista=charge.list_amount_usd,
        descuento=charge.discount_usd,
        credito=charge.credit_usd,
        monto=charge.amount_usd,
        cupon=charge.coupon_code,
        estado=charge.status,
        vence=charge.due_at,
        instrucciones=checkout.instructions,
    )


def subscription_out(session: Session, subscription: Subscription) -> SubscriptionOut:
    plan = session.get(Plan, subscription.plan_id)
    pending = (
        session.get(Plan, subscription.pending_plan_id) if subscription.pending_plan_id else None
    )
    return SubscriptionOut(
        plan=subscription.plan_id,
        plan_nombre=plan.name if plan else subscription.plan_id,
        estado=subscription.status,
        ciclo=subscription.billing_cycle,
        precio=subscription.price_locked_usd,
        desde=subscription.current_period_start,
        hasta=subscription.current_period_end,
        gracia_hasta=subscription.grace_until,
        cancela_al_final=subscription.cancel_at_period_end,
        cambio_programado=pending.name if pending else None,
    )


# ------------------------------------------------------------------- routes


@router.get("/plans", response_model=Catalog, summary="Plans, prices and packs")
async def plans(session: SessionDep, principal: ConfigurationManagerDep) -> Catalog:
    rows = session.execute(
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.price_usd_month.is_not(None))
        .order_by(Plan.price_usd_month)
    ).scalars()
    addons = session.execute(select(PlanAddon).where(PlanAddon.is_active.is_(True))).scalars()
    settings = get_settings()
    return Catalog(
        planes=[
            PlanOut(
                id=p.id,
                nombre=p.name,
                precio_mensual=price_for(p, "monthly"),
                precio_anual=price_for(p, "annual"),
                asientos=p.max_seats,
                mensajes=p.max_ai_messages,
                caracteres_conocimiento=p.max_rag_chars,
                fuentes_conocimiento=p.max_knowledge_files,
                niveles=list(p.allowed_intelligence),
            )
            for p in rows
        ],
        paquetes=[
            AddonOut(id=a.id, nombre=a.name, mensajes=a.ai_messages, precio=money(a.price_usd))
            for a in addons
        ],
        soles_por_dolar=settings.billing_pen_reference_rate,
        instrucciones_pago=settings.billing_payment_instructions,
    )


@router.get("", response_model=BillingOut, summary="Subscription, usage, charges and payments")
async def overview(session: SessionDep, principal: ConfigurationManagerDep) -> BillingOut:
    service = _service(session, principal.tenant_id)
    tenant = service.tenant()
    subscription = service.subscriptions.current()
    period = service.usage.open_period()
    return BillingOut(
        estado_negocio=tenant.status,
        prueba_hasta=tenant.trial_ends_at if tenant.status == "trial" else None,
        suscripcion=subscription_out(session, subscription) if subscription else None,
        consumo=UsageOut(
            usados=tenant.ai_messages_used,
            limite=message_limit(session, tenant),
            desde=period.period_start if period else None,
            hasta=period.period_end if period else None,
        ),
        pendientes=[charge_out(session, c) for c in service.charges.pending()],
        pagos=[
            PaymentOut(
                id=p.id,
                comprobante=p.receipt_number,
                concepto=concept(session, session.get(Charge, p.charge_id))  # type: ignore[arg-type]
                if p.charge_id
                else p.kind,
                monto=p.amount_usd,
                estado=p.status,
                en=p.created_at,
            )
            for p in service.payments.history()
        ],
        cobro_activo=billing_enforced(),
    )


_ERRORS = error_responses(
    (400, "`plan_not_purchasable` or `coupon_invalid`"),
    (403, "`insufficient_role`: only the owner buys"),
    (404, "`unknown_plan` or `not_found`"),
    (409, "`use_change_plan`, `no_active_subscription`, `same_plan`, `downgrade_blocked`"),
)


@router.post("/checkout", response_model=ChargeOut, responses=_ERRORS, summary="Buy a plan")
async def checkout(
    body: CheckoutBody, session: SessionDep, principal: AccountOwnerDep
) -> ChargeOut:
    service = _service(session, principal.tenant_id)
    try:
        charge = service.checkout(body.plan_id, body.ciclo, coupon=body.cupon or None)
    except BillingError as exc:
        raise billing_error(exc) from exc
    session.commit()
    return charge_out(session, charge)


@router.post(
    "/change-plan", response_model=ChangeResult, responses=_ERRORS, summary="Upgrade or downgrade"
)
async def change_plan(
    body: ChangePlanBody, session: SessionDep, principal: AccountOwnerDep
) -> ChangeResult:
    service = _service(session, principal.tenant_id)
    try:
        result = service.change_plan(body.plan_id, body.ciclo)
    except BillingError as exc:
        raise billing_error(exc) from exc
    session.commit()
    if isinstance(result, Charge):
        return ChangeResult(cobro=charge_out(session, result))
    return ChangeResult(programado=subscription_out(session, result))


@router.post("/cancel", response_model=SubscriptionOut, responses=_ERRORS)
async def cancel(session: SessionDep, principal: AccountOwnerDep) -> SubscriptionOut:
    service = _service(session, principal.tenant_id)
    try:
        subscription = service.cancel()
    except BillingError as exc:
        raise billing_error(exc) from exc
    session.commit()
    return subscription_out(session, subscription)


@router.post("/resume", response_model=SubscriptionOut, responses=_ERRORS)
async def resume(session: SessionDep, principal: AccountOwnerDep) -> SubscriptionOut:
    service = _service(session, principal.tenant_id)
    try:
        subscription = service.resume()
    except BillingError as exc:
        raise billing_error(exc) from exc
    session.commit()
    return subscription_out(session, subscription)


@router.post("/addons/{addon_id}/checkout", response_model=ChargeOut, responses=_ERRORS)
async def addon_checkout(
    addon_id: str, session: SessionDep, principal: AccountOwnerDep
) -> ChargeOut:
    service = _service(session, principal.tenant_id)
    try:
        charge = service.addon_checkout(addon_id)
    except BillingError as exc:
        raise billing_error(exc) from exc
    session.commit()
    return charge_out(session, charge)


@router.post("/charges/{charge_id}/cancel", response_model=ChargeOut, responses=_ERRORS)
async def cancel_charge(
    charge_id: uuid.UUID, session: SessionDep, principal: AccountOwnerDep
) -> ChargeOut:
    service = _service(session, principal.tenant_id)
    try:
        charge = service.cancel_charge(charge_id)
    except BillingError as exc:
        raise billing_error(exc) from exc
    session.commit()
    return charge_out(session, charge)


@router.get(
    "/payments/{payment_id}/receipt.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}, 404: {"description": "`not_found`"}},
    summary="The receipt of one payment",
)
async def receipt(
    payment_id: uuid.UUID, session: SessionDep, principal: ConfigurationManagerDep
) -> Response:
    service = _service(session, principal.tenant_id)
    payment = service.payments.get(payment_id)
    if payment is None:
        raise billing_error(ChargeNotFound(str(payment_id)))
    charge = session.get(Charge, payment.charge_id)
    tenant = service.tenant()
    local = (
        f"{payment.currency_local or ''} {payment.amount_local}".strip()
        if payment.amount_local is not None
        else None
    )
    pdf = render_receipt(
        ReceiptData(
            number=payment.receipt_number,
            issued_at=payment.created_at,
            business=tenant.name,
            concept=concept(session, charge) if charge else payment.kind,
            reference=charge.reference if charge else "",
            amount_usd=f"US$ {payment.amount_usd}",
            paid_local=local,
            method="Transferencia o Yape" if payment.provider == "manual" else payment.provider,
        )
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{payment.receipt_number}.pdf"'},
    )
