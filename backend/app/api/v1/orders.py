"""Orders: the list, the summary, the detail and the operator's decisions (TASK-430).

The `/tenant/orders` section of `contracts/api.md` (section 5). Every member of the
business can read and decide: "operador" is any active member. Money is `Decimal`
and travels as a string (`"500.00"`); the customer's phone is masked in the list and
complete in the detail, which only members of the tenant can open (C-20).

The decisions (confirm, reject, status) commit in `OrderReviewService`; the notice to
the customer goes out *after* that, here, and is reported in `aviso_cliente` without
ever undoing the decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from app.api.deps import (
    EmailProviderDep,
    MemberDep,
    SessionDep,
    WhatsAppProviderDep,
    get_storage_provider,
)
from app.api.errors import ApiError, error_responses
from app.core.config import get_settings
from app.models.channel import Contact
from app.models.orders import ORDER_STATES, Order, OrderItem, PaymentProof
from app.orders.review import (
    OPERATOR_STATES,
    AlreadyReviewed,
    OrderNotFound,
    OrderReviewService,
    ReasonRequired,
)
from app.orders.service import InvalidTransition, customer_snapshot
from app.providers.storage.base import StorageProvider
from app.providers.whatsapp.base import WhatsAppProvider
from app.repositories.audit import AuditLogRepository
from app.repositories.business import BusinessProfileRepository
from app.repositories.channel import ContactRepository, session_window_is_open
from app.repositories.orders import OrderItemRepository, OrderRepository, PaymentProofRepository
from app.services.order_notices import CustomerNotice, CustomerNotifier
from app.services.tenant_alerts import TenantAlerts, send_emails

router = APIRouter(prefix="/tenant/orders", tags=["orders"])

StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
NOT_FOUND = (404, "`not_found`: the order does not exist or is another business's")
CONFLICT = (409, "`invalid_transition` or `already_reviewed`")
VALIDATION = (400, "`validation_error`: discarding an order with a payment proof needs `motivo`")

MAX_PAGE_SIZE = 50
# The link to a receipt opens a private object: short on purpose (C-20).
IMAGE_LINK_SECONDS = 600
MAX_NEXT_STEP = 300

Period = Literal["hoy", "7d", "30d"]
Motivo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300)]
NextStep = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_NEXT_STEP)]


# ------------------------------------------------------------------ schemas


class CustomerBrief(BaseModel):
    nombre: str | None
    telefono: str | None


class OrderListItem(BaseModel):
    id: uuid.UUID
    numero: int
    estado: str
    cliente: CustomerBrief
    total: Decimal
    moneda: str
    items_resumen: str
    tiene_comprobante: bool
    observaciones: list[str]
    # Discarded after the payment was confirmed: the customer is owed a refund.
    devolucion_pendiente: bool
    creado: datetime


class OrderList(BaseModel):
    items: list[OrderListItem]
    total: int
    page: int


class CurrencySales(BaseModel):
    moneda: str
    conversiones: int
    valor_total: Decimal
    ticket_promedio: Decimal


class OrderSummary(BaseModel):
    # Every sale, whatever its currency.
    conversiones: int
    # Value and average ticket in the business's currency only (`moneda`).
    valor_total: Decimal
    ticket_promedio: Decimal
    moneda: str
    # One row per currency sold in the period, the business's first.
    por_moneda: list[CurrencySales]
    por_estado: dict[str, int]
    pendientes_revision: int


class LineOut(BaseModel):
    producto_id: uuid.UUID | None
    nombre: str
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal
    selecciones: list[str]
    faltan_selecciones: int


class ProofOut(BaseModel):
    """A receipt as the panel shows it: no storage key, no URL (those are separate)."""

    id: uuid.UUID
    estado: str
    monto: Decimal | None
    moneda: str | None
    fecha_operacion: date | None
    codigo_operacion: str | None
    entidad: str | None
    destinatario: str | None
    confianza: Decimal | None
    observaciones: list[str]
    motivo_rechazo: str | None
    creado: datetime
    revisado: datetime | None


class HistoryEntry(BaseModel):
    evento: str
    estado: str | None
    en: datetime
    usuario_id: uuid.UUID | None = None


class OrderDetail(BaseModel):
    id: uuid.UUID
    numero: int
    estado: str
    modalidad_pago: str
    moneda: str
    items: list[LineOut]
    subtotal: Decimal
    descuento: Decimal
    envio: Decimal
    total: Decimal
    promocion: dict[str, Any] | None
    tipo_entrega: str | None
    zona_envio: str | None
    datos_cliente: dict[str, Any]
    comprobantes: list[ProofOut]
    motivo_rechazo: str | None
    rechazo_vigente: bool
    devolucion_pendiente: bool
    siguiente_paso: str | None
    es_sandbox: bool
    conversation_id: uuid.UUID
    ventana_abierta: bool
    creado: datetime
    historial: list[HistoryEntry]


class ProofImage(BaseModel):
    url: str
    expires_at: datetime


class ConfirmRequest(BaseModel):
    siguiente_paso: NextStep | None = None


class RejectRequest(BaseModel):
    motivo: Motivo


class StatusRequest(BaseModel):
    estado: Literal["en_curso", "completado", "descartado"]
    # Required to discard an order with a payment proof: the customer is told why.
    motivo: Motivo | None = None


class DecisionResponse(BaseModel):
    estado: str
    aviso_cliente: CustomerNotice


class StatusResponse(BaseModel):
    estado: str
    # Only when the customer was told (a discarded order they had paid for).
    aviso_cliente: CustomerNotice | None = None
    devolucion_pendiente: bool = False


# ---------------------------------------------------------------- presenters


def mask_phone(phone: str | None) -> str | None:
    """`+51987654628` -> `+51 9•• ••• 628`: enough to recognise, not to copy."""
    if not phone:
        return None
    digits = phone.lstrip("+")
    if phone.startswith("+51") and len(digits) == 11:
        return f"+51 {digits[2]}•• ••• {digits[-3:]}"
    keep = digits[-3:]
    return "+" + "•" * max(0, len(digits) - 3) + keep


def short_name(contact: Contact, order: Order) -> str | None:
    """`Ana Pérez Soto` -> `Ana P.`: the name the customer gave, else WhatsApp's label."""
    given = (order.datos_cliente or {}).get("nombres") or (contact.custom_data or {}).get("nombres")
    full = given if isinstance(given, str) and given.strip() else contact.profile_name
    if not full or not full.strip():
        return None
    parts = full.split()
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[1][0].upper()}."


def items_summary(items: list[OrderItem]) -> str:
    """`2 × Polo, Gorra`, bounded: a line of the list, not a receipt."""
    text = ", ".join(
        f"{item.cantidad} × {item.nombre}" if item.cantidad > 1 else item.nombre for item in items
    )
    return text if len(text) <= 120 else text[:119].rstrip() + "…"


def line_out(item: OrderItem) -> LineOut:
    selections = [str(chosen.get("nombre", "")) for chosen in item.bundle_selections]
    missing = (
        max(0, (item.bundle_size or 0) * item.cantidad - len(selections)) if item.bundle_size else 0
    )
    return LineOut(
        producto_id=item.product_id,
        nombre=item.nombre,
        cantidad=item.cantidad,
        precio_unitario=item.precio_unitario,
        subtotal=item.subtotal,
        selecciones=selections,
        faltan_selecciones=missing,
    )


def proof_out(proof: PaymentProof) -> ProofOut:
    return ProofOut(
        id=proof.id,
        estado=proof.estado,
        monto=proof.monto,
        moneda=proof.moneda,
        fecha_operacion=proof.fecha_operacion,
        codigo_operacion=proof.codigo_operacion,
        entidad=proof.entidad,
        destinatario=proof.destinatario,
        confianza=proof.confianza,
        observaciones=list(proof.observaciones),
        motivo_rechazo=proof.motivo_rechazo,
        creado=proof.created_at,
        revisado=proof.reviewed_at,
    )


# ------------------------------------------------------------------- helpers


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="not_found", message="Order not found.")


def _conflict(error: str, exc: Exception) -> ApiError:
    return ApiError(status_code=status.HTTP_409_CONFLICT, error=error, message=str(exc))


def _period_start(period: Period, session: Session, tenant_id: uuid.UUID) -> datetime:
    """Where a summary period begins; "today" is the business's own day."""
    now = datetime.now(UTC)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    profile = BusinessProfileRepository(session, tenant_id).current()
    try:
        zone = ZoneInfo(profile.timezone if profile else "America/Lima")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/Lima")
    return now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)


def _day_start(day: date, *, end: bool = False) -> datetime:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start + timedelta(days=1) if end else start


def _currency(session: Session, tenant_id: uuid.UUID) -> str:
    profile = BusinessProfileRepository(session, tenant_id).current()
    return (profile.currency if profile else None) or "PEN"


# ------------------------------------------------------------------ endpoints


@router.get(
    "",
    summary="List the orders",
    responses=error_responses(*AUTH_RESPONSES),
)
def list_orders(
    principal: MemberDep,
    session: SessionDep,
    estado: Annotated[list[str] | None, Query()] = None,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sandbox: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> OrderList:
    """Newest first, with the customer's phone masked. Simulator orders are hidden unless asked."""
    unknown = [state for state in (estado or []) if state not in ORDER_STATES]
    if unknown:
        raise ApiError(
            status_code=400,
            error="validation_error",
            message="Unknown order state.",
            fields={"estado": ", ".join(unknown)},
        )
    orders = OrderRepository(session, principal.tenant_id)
    rows, total = orders.search(
        estados=estado or (),
        since=_day_start(desde) if desde else None,
        until=_day_start(hasta, end=True) if hasta else None,
        q=q,
        sandbox=sandbox,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    ids = [order.id for order, _ in rows]
    lines = OrderItemRepository(session, principal.tenant_id).for_orders(ids)
    proofs = PaymentProofRepository(session, principal.tenant_id).for_orders(ids)
    items = [
        OrderListItem(
            id=order.id,
            numero=order.numero,
            estado=order.estado,
            cliente=CustomerBrief(
                nombre=short_name(contact, order), telefono=mask_phone(contact.phone_e164)
            ),
            total=order.total,
            moneda=order.moneda,
            items_resumen=items_summary(lines[order.id]),
            tiene_comprobante=bool(proofs[order.id]),
            observaciones=list(proofs[order.id][0].observaciones) if proofs[order.id] else [],
            devolucion_pendiente=order.devolucion_pendiente,
            creado=order.created_at,
        )
        for order, contact in rows
    ]
    return OrderList(items=items, total=total, page=page)


@router.get(
    "/summary",
    summary="Conversions, value and what is waiting, for a period",
    responses=error_responses(*AUTH_RESPONSES),
)
def orders_summary(
    principal: MemberDep,
    session: SessionDep,
    periodo: Annotated[Period, Query()] = "hoy",
) -> OrderSummary:
    """`conversiones` are the orders that reached `confirmado` or beyond in the period.

    The simulator's orders never count (C-19).
    """
    orders = OrderRepository(session, principal.tenant_id)
    since = _period_start(periodo, session, principal.tenant_id)
    currency = _currency(session, principal.tenant_id)
    rows = {
        moneda: _currency_sales(moneda, count, total)
        for moneda, count, total in orders.sales_by_currency(since=since)
    }
    own = rows.pop(currency, _currency_sales(currency, 0, Decimal("0")))
    by_state = orders.totals_by_state(since=since)
    return OrderSummary(
        conversiones=own.conversiones + sum(row.conversiones for row in rows.values()),
        valor_total=own.valor_total,
        ticket_promedio=own.ticket_promedio,
        moneda=currency,
        por_moneda=[own, *rows.values()] if own.conversiones else list(rows.values()),
        por_estado={state: by_state.get(state, 0) for state in ORDER_STATES},
        pendientes_revision=by_state.get("por_confirmar", 0),
    )


def _currency_sales(moneda: str, count: int, total: Decimal) -> CurrencySales:
    ticket = (total / count).quantize(Decimal("0.01"), ROUND_HALF_UP) if count else Decimal("0.00")
    return CurrencySales(
        moneda=moneda,
        conversiones=count,
        valor_total=total.quantize(Decimal("0.01")),
        ticket_promedio=ticket,
    )


@router.get(
    "/{order_id}",
    summary="One order with its items, customer data, receipts and history",
    responses=error_responses(NOT_FOUND, *AUTH_RESPONSES),
)
def get_order(order_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> OrderDetail:
    orders = OrderRepository(session, principal.tenant_id)
    order = orders.get(order_id)
    if order is None:
        raise _not_found()
    contact = ContactRepository(session, principal.tenant_id).get(order.contact_id)
    lines = OrderItemRepository(session, principal.tenant_id).for_order(order.id)
    proofs = list(PaymentProofRepository(session, principal.tenant_id).for_order(order.id))
    data = dict(order.datos_cliente) if order.datos_cliente else {}
    if not data and contact is not None:
        data = customer_snapshot(contact)
    history = [
        HistoryEntry(evento="creado", estado="borrador", en=order.created_at),
        *[
            HistoryEntry(evento="comprobante_recibido", estado="por_confirmar", en=proof.created_at)
            for proof in proofs
        ],
        *[
            HistoryEntry(
                evento=entry.action,
                estado=str(entry.detail.get("a")) if entry.detail.get("a") else None,
                en=entry.created_at,
                usuario_id=entry.actor_id,
            )
            for entry in AuditLogRepository(session, principal.tenant_id).for_order(order.id)
        ],
    ]
    return OrderDetail(
        id=order.id,
        numero=order.numero,
        estado=order.estado,
        modalidad_pago=order.modalidad_pago,
        moneda=order.moneda,
        items=[line_out(item) for item in lines],
        subtotal=order.subtotal,
        descuento=order.descuento,
        envio=order.envio,
        total=order.total,
        promocion=order.promotion_snapshot,
        tipo_entrega=order.tipo_entrega,
        zona_envio=order.zona_envio,
        datos_cliente=data,
        comprobantes=[proof_out(proof) for proof in proofs],
        motivo_rechazo=order.motivo_rechazo,
        rechazo_vigente=order.rechazo_vigente,
        devolucion_pendiente=order.devolucion_pendiente,
        siguiente_paso=order.siguiente_paso,
        es_sandbox=order.is_sandbox,
        conversation_id=order.conversation_id,
        ventana_abierta=(
            order.is_sandbox
            or (contact is not None and session_window_is_open(contact, now=datetime.now(UTC)))
        ),
        creado=order.created_at,
        historial=sorted(history, key=lambda entry: entry.en),
    )


@router.get(
    "/{order_id}/proofs/{proof_id}/image",
    summary="A signed link to the receipt photo (10 minutes)",
    responses=error_responses(NOT_FOUND, *AUTH_RESPONSES),
)
def proof_image(
    order_id: uuid.UUID,
    proof_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    storage: StorageDep,
) -> ProofImage:
    """The photo is private: only members of the tenant get a link, and it expires."""
    proof = PaymentProofRepository(session, principal.tenant_id).get(proof_id)
    if proof is None or proof.order_id != order_id:
        raise _not_found()
    return ProofImage(
        url=storage.presigned_url(proof.storage_key, expires_in=IMAGE_LINK_SECONDS),
        expires_at=datetime.now(UTC) + timedelta(seconds=IMAGE_LINK_SECONDS),
    )


@router.post(
    "/{order_id}/confirm",
    summary="Accept the payment (or the cash-on-delivery order)",
    responses=error_responses(NOT_FOUND, CONFLICT, *AUTH_RESPONSES),
)
async def confirm_order(
    order_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    provider: WhatsAppProviderDep,
    email: EmailProviderDep,
    body: ConfirmRequest | None = None,
) -> DecisionResponse:
    """Order, receipt, audit and stage in one transaction; the customer is told after."""
    next_step = body.siguiente_paso if body else None
    review = OrderReviewService(session, principal.tenant_id)
    try:
        result = review.confirm(order_id, principal.user_id, siguiente_paso=next_step)
    except OrderNotFound as exc:
        raise _not_found() from exc
    except AlreadyReviewed as exc:
        raise _conflict("already_reviewed", exc) from exc
    except InvalidTransition as exc:
        raise _conflict("invalid_transition", exc) from exc

    alerts = TenantAlerts(session, principal.tenant_id)
    notice = await _notifier(session, principal.tenant_id, provider, alerts).notify(
        result.order, "pago_confirmado", siguiente_paso=result.order.siguiente_paso
    )
    await send_emails(email, alerts.pending_emails)
    return DecisionResponse(estado=result.order.estado, aviso_cliente=notice)


@router.post(
    "/{order_id}/reject",
    summary="Send the order back with a reason",
    responses=error_responses(NOT_FOUND, CONFLICT, *AUTH_RESPONSES),
)
async def reject_order(
    order_id: uuid.UUID,
    body: RejectRequest,
    principal: MemberDep,
    session: SessionDep,
    provider: WhatsAppProviderDep,
    email: EmailProviderDep,
) -> DecisionResponse:
    review = OrderReviewService(session, principal.tenant_id)
    try:
        result = review.reject(order_id, principal.user_id, motivo=body.motivo)
    except OrderNotFound as exc:
        raise _not_found() from exc
    except AlreadyReviewed as exc:
        raise _conflict("already_reviewed", exc) from exc
    except InvalidTransition as exc:
        raise _conflict("invalid_transition", exc) from exc

    assert result.notice is not None  # a rejection always tells the customer
    alerts = TenantAlerts(session, principal.tenant_id)
    notice = await _notifier(session, principal.tenant_id, provider, alerts).notify(
        result.order, result.notice, motivo=body.motivo
    )
    await send_emails(email, alerts.pending_emails)
    return DecisionResponse(estado=result.order.estado, aviso_cliente=notice)


@router.post(
    "/{order_id}/status",
    summary="Move a confirmed order along, or discard it",
    responses=error_responses(NOT_FOUND, CONFLICT, VALIDATION, *AUTH_RESPONSES),
)
async def set_order_status(
    order_id: uuid.UUID,
    body: StatusRequest,
    principal: MemberDep,
    session: SessionDep,
    provider: WhatsAppProviderDep,
    email: EmailProviderDep,
) -> StatusResponse:
    """Discarding an order with a payment proof needs `motivo`, voids the proof and
    tells the customer; if it was confirmed, the order owes a refund."""
    assert body.estado in OPERATOR_STATES
    review = OrderReviewService(session, principal.tenant_id)
    try:
        result = review.set_status(order_id, principal.user_id, body.estado, motivo=body.motivo)
    except OrderNotFound as exc:
        raise _not_found() from exc
    except InvalidTransition as exc:
        raise _conflict("invalid_transition", exc) from exc
    except ReasonRequired as exc:
        raise ApiError(
            status_code=400,
            error="validation_error",
            message="A reason is required to discard an order with a payment proof.",
            fields={"motivo": "required"},
        ) from exc

    notice: CustomerNotice | None = None
    if result.notice is not None:
        alerts = TenantAlerts(session, principal.tenant_id)
        notice = await _notifier(session, principal.tenant_id, provider, alerts).notify(
            result.order, result.notice, motivo=body.motivo
        )
        await send_emails(email, alerts.pending_emails)
    return StatusResponse(
        estado=result.order.estado,
        aviso_cliente=notice,
        devolucion_pendiente=result.order.devolucion_pendiente,
    )


def _notifier(
    session: Session, tenant_id: uuid.UUID, provider: WhatsAppProvider, alerts: TenantAlerts
) -> CustomerNotifier:
    return CustomerNotifier(
        session,
        tenant_id,
        provider=provider,
        max_attempts=get_settings().channel_send_max_attempts,
        notices=alerts,
    )
