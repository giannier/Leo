"""After the sale: returns and the documents of an order (feature 019, phase G).

    GET  /tenant/returns                               the requests, the waiting ones first
    GET  /tenant/returns/{id}                          one request, with what happened to it
    POST /tenant/returns                               register one a customer phoned in
    POST /tenant/returns/{id}/decision                 approve, reject, resolve or cancel it
    GET  /tenant/orders/{id}/documents                 the boleta, the factura, the guía of an order
    POST /tenant/orders/{id}/documents                 attach a PDF or an image
    POST /tenant/orders/{id}/documents/summary         make the order's summary PDF
    GET  /tenant/orders/{id}/documents/{doc}/link      a short-lived link to open it
    DELETE /tenant/orders/{id}/documents/{doc}         remove what was attached by mistake
    POST /tenant/orders/{id}/documents/{doc}/void      void one

Any member of the business may read and decide, like the orders: «operador» is any active member.
The customer is told the decision in their own chat, after it committed, and never undoes it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints

from app.aftersale.documents import DocumentError, DocumentService, label_of
from app.aftersale.returns import ReturnError, ReturnService, customer_notice
from app.api.deps import MemberDep, SessionDep, WhatsAppProviderDep, get_storage_provider
from app.api.errors import ApiError, error_responses
from app.api.v1.orders import mask_phone, short_name
from app.core.config import get_settings
from app.models.aftersale import DOCUMENT_TYPES, RETURN_STATES, OrderDocument, OrderReturn
from app.models.channel import Contact
from app.models.orders import Order
from app.providers.storage.base import StorageProvider
from app.repositories.aftersale import (
    DocumentRepository,
    ReturnEventRepository,
    ReturnRepository,
)
from app.repositories.channel import ContactRepository
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.services.crm_report import contact_name
from app.services.media_library import MAX_UPLOAD
from app.services.order_notices import CustomerNotice, CustomerNotifier
from app.services.tenant_alerts import TenantAlerts

router = APIRouter(prefix="/tenant", tags=["aftersale"])

StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
NOT_FOUND = (404, "`not_found`")

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


# ------------------------------------------------------------------ schemas


class ReturnRow(BaseModel):
    id: uuid.UUID
    numero: int
    pedido_id: uuid.UUID
    pedido: int
    tipo: str
    motivo: str
    estado: str
    cliente: str | None
    telefono: str | None
    detalle: str
    creada: datetime
    resuelta: datetime | None


class ReturnList(BaseModel):
    items: list[ReturnRow]
    total: int
    pendientes: int


class ReturnItemOut(BaseModel):
    nombre: str
    cantidad: int


class ReturnEventOut(BaseModel):
    desde: str | None
    hacia: str
    actor: str
    en: datetime
    resolucion: str | None


class ReturnDetail(ReturnRow):
    items: list[ReturnItemOut]
    resolucion: str | None
    conversation_id: uuid.UUID | None
    fotos: int
    eventos: list[ReturnEventOut]
    total_pedido: Decimal
    moneda: str
    estado_pedido: str
    creada_por: str


class ReturnIn(BaseModel):
    order_id: uuid.UUID
    tipo: Literal["devolucion", "cambio", "garantia", "reclamo"]
    motivo: Literal["defectuoso", "equivocado", "incompleto", "no_conforme", "tardio", "otro"]
    detalle: Text = ""
    productos: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]] = (
        Field(default_factory=list, max_length=10)
    )


class DecisionIn(BaseModel):
    a: Literal["aprobada", "rechazada", "resuelta", "cancelada"]
    # What the customer will read: what happens next, or why not.
    resolucion: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = (
        None
    )


class DecisionOut(BaseModel):
    solicitud: ReturnDetail
    aviso_cliente: CustomerNotice | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    tipo: str
    titulo: str
    nombre: str
    serie: str | None
    numero: str | None
    origen: str
    estado: str
    archivo: str
    tamano: int
    creado: datetime
    enviado: datetime | None


class DocumentList(BaseModel):
    items: list[DocumentOut]


class LinkOut(BaseModel):
    url: str


# ----------------------------------------------------------------- helpers


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="not_found", message="Not found.")


def _row(ret: OrderReturn, order: Order, contact: Contact) -> ReturnRow:
    return ReturnRow(
        id=ret.id,
        numero=ret.numero,
        pedido_id=order.id,
        pedido=order.numero,
        tipo=ret.tipo,
        motivo=ret.motivo,
        estado=ret.estado,
        cliente=short_name(contact, order),
        telefono=mask_phone(contact.phone_e164),
        detalle=ret.detalle,
        creada=ret.created_at,
        resuelta=ret.resuelta_at,
    )


def _detail(session: SessionDep, tenant_id: uuid.UUID, ret: OrderReturn) -> ReturnDetail:
    order = OrderRepository(session, tenant_id).get(ret.order_id)
    contact = ContactRepository(session, tenant_id).get(ret.contact_id)
    if order is None or contact is None:  # pragma: no cover - the foreign keys hold them
        raise _not_found()
    events = ReturnEventRepository(session, tenant_id).of_return(ret.id)
    base = _row(ret, order, contact)
    return ReturnDetail(
        **base.model_dump(),
        items=[
            ReturnItemOut(nombre=str(i.get("nombre", "")), cantidad=int(i.get("cantidad", 1)))
            for i in ret.items
        ],
        resolucion=ret.resolucion,
        conversation_id=ret.conversation_id,
        fotos=len(ret.evidencia),
        eventos=[
            ReturnEventOut(
                desde=e.desde,
                hacia=e.hacia,
                actor=e.actor,
                en=e.created_at,
                resolucion=(e.detalle or {}).get("resolucion"),
            )
            for e in events
        ],
        total_pedido=order.total,
        moneda=order.moneda,
        estado_pedido=order.estado,
        creada_por=ret.created_by,
    )


def _document_out(document: OrderDocument) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        tipo=document.tipo,
        titulo=document.titulo,
        nombre=label_of(document),
        serie=document.serie,
        numero=document.numero,
        origen=document.origen,
        estado=document.estado,
        archivo=document.filename,
        tamano=document.size_bytes,
        creado=document.created_at,
        enviado=document.enviado_at,
    )


def _error(exc: ReturnError | DocumentError) -> ApiError:
    status_code = {
        "not_found": 404,
        "invalid_transition": 409,
        "order_not_sold": 409,
        "issued": 409,
        "storage_unavailable": 503,
    }.get(exc.code, 422)
    return ApiError(status_code=status_code, error=exc.code, message=str(exc))


def _own_order(session: SessionDep, tenant_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    order = OrderRepository(session, tenant_id).get(order_id)
    if order is None:
        raise _not_found()
    return order


def _own_document(
    session: SessionDep, tenant_id: uuid.UUID, order: Order, document_id: uuid.UUID
) -> OrderDocument:
    document = DocumentRepository(session, tenant_id).get(document_id)
    if document is None or document.order_id != order.id:
        raise _not_found()
    return document


# ------------------------------------------------------------------ returns


@router.get(
    "/returns", summary="The returns, exchanges and claims", responses=error_responses(*AUTH)
)
def list_returns(
    principal: MemberDep,
    session: SessionDep,
    estado: Annotated[list[str] | None, Query()] = None,
    pedido_id: Annotated[uuid.UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 30,
) -> ReturnList:
    unknown = [s for s in (estado or []) if s not in RETURN_STATES]
    if unknown:
        raise ApiError(
            status_code=400,
            error="validation_error",
            message="Unknown state.",
            fields={"estado": ", ".join(unknown)},
        )
    repo = ReturnRepository(session, principal.tenant_id)
    rows, total = repo.search(
        estados=estado or (),
        order_id=pedido_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return ReturnList(
        items=[_row(r, o, c) for r, o, c in rows], total=total, pendientes=repo.count_waiting()
    )


@router.get(
    "/returns/{return_id}",
    summary="One request",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def get_return(return_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> ReturnDetail:
    ret = ReturnRepository(session, principal.tenant_id).get(return_id)
    if ret is None:
        raise _not_found()
    return _detail(session, principal.tenant_id, ret)


@router.post(
    "/returns",
    status_code=status.HTTP_201_CREATED,
    summary="Register a request a customer made outside the chat",
    responses=error_responses(
        NOT_FOUND,
        (409, "`order_not_sold`: only a confirmed order can have one"),
        (422, "`item_not_in_order`"),
        *AUTH,
    ),
)
def create_return(body: ReturnIn, principal: MemberDep, session: SessionDep) -> ReturnDetail:
    order = _own_order(session, principal.tenant_id, body.order_id)
    lines = list(OrderItemRepository(session, principal.tenant_id).for_orders([order.id])[order.id])
    try:
        opened = ReturnService(session, principal.tenant_id).open(
            order,
            tipo=body.tipo,
            motivo=body.motivo,
            detalle=body.detalle,
            items=[{"nombre": name} for name in body.productos],
            lines=lines,
            actor="user",
            user_id=principal.user_id,
        )
    except ReturnError as exc:
        raise _error(exc) from exc
    if not opened.created:
        raise ApiError(
            status_code=409,
            error="already_open",
            message=f"Ya hay una solicitud abierta de ese tipo: #{opened.ret.numero}.",
        )
    session.commit()
    return _detail(session, principal.tenant_id, opened.ret)


@router.post(
    "/returns/{return_id}/decision",
    summary="Approve, reject, resolve or cancel a request",
    responses=error_responses(
        NOT_FOUND,
        (409, "`invalid_transition`"),
        (422, "`resolution_required`: approving or rejecting says what happens, or why not"),
        *AUTH,
    ),
)
async def decide_return(
    return_id: uuid.UUID,
    body: DecisionIn,
    principal: MemberDep,
    session: SessionDep,
    provider: WhatsAppProviderDep,
) -> DecisionOut:
    """Decides first; the customer is told after, and a notice that fails never undoes it."""
    repo = ReturnRepository(session, principal.tenant_id)
    ret = repo.get_for_update(return_id)
    if ret is None:
        raise _not_found()
    order = OrderRepository(session, principal.tenant_id).get(ret.order_id)
    contact = ContactRepository(session, principal.tenant_id).get(ret.contact_id)
    if order is None or contact is None:  # pragma: no cover - the foreign keys hold them
        raise _not_found()
    try:
        ReturnService(session, principal.tenant_id).decide(
            ret, to=body.a, user_id=principal.user_id, resolucion=body.resolucion
        )
    except ReturnError as exc:
        raise _error(exc) from exc

    alerts = TenantAlerts(session, principal.tenant_id)
    notifier = CustomerNotifier(
        session,
        principal.tenant_id,
        provider=provider,
        max_attempts=get_settings().channel_send_max_attempts,
        notices=alerts,
    )
    text = customer_notice(ret, order, name=_first_name(contact))
    prepared = notifier.prepare_text(order, text) if text else None
    session.commit()
    notice = await notifier.deliver(prepared) if prepared else None
    return DecisionOut(solicitud=_detail(session, principal.tenant_id, ret), aviso_cliente=notice)


def _first_name(contact: Contact) -> str | None:
    name = contact_name(contact)
    return name.split()[0] if name else None


# ---------------------------------------------------------------- documents


@router.get(
    "/orders/{order_id}/documents",
    summary="The documents of an order",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def list_documents(order_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> DocumentList:
    order = _own_order(session, principal.tenant_id, order_id)
    documents = DocumentRepository(session, principal.tenant_id).of_order(order.id)
    return DocumentList(items=[_document_out(d) for d in documents])


@router.post(
    "/orders/{order_id}/documents",
    status_code=status.HTTP_201_CREATED,
    summary="Attach a boleta, a factura, a guía or a warranty to an order",
    responses=error_responses(
        NOT_FOUND,
        (422, "`unsupported_file`, `file_too_large`, `empty_file`, `invalid_type`, `too_many`"),
        (503, "`storage_unavailable`"),
        *AUTH,
    ),
)
async def attach_document(
    order_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
    tipo: Annotated[str, Form()],
    titulo: Annotated[str | None, Form()] = None,
    serie: Annotated[str | None, Form()] = None,
    numero: Annotated[str | None, Form()] = None,
) -> DocumentOut:
    order = _own_order(session, principal.tenant_id, order_id)
    if tipo not in DOCUMENT_TYPES:
        raise ApiError(status_code=422, error="invalid_type", message="Unknown document type.")
    data = await file.read(MAX_UPLOAD + 1)
    try:
        document = DocumentService(session, principal.tenant_id, storage).attach(
            order,
            data,
            file.filename or "",
            tipo=tipo,
            titulo=titulo,
            serie=serie,
            numero=numero,
            user_id=principal.user_id,
        )
    except DocumentError as exc:
        raise _error(exc) from exc
    session.commit()
    return _document_out(document)


@router.post(
    "/orders/{order_id}/documents/summary",
    status_code=status.HTTP_201_CREATED,
    summary="Make a PDF with the summary of the order",
    responses=error_responses(NOT_FOUND, (422, "`too_many`"), *AUTH),
)
def make_summary(
    order_id: uuid.UUID, principal: MemberDep, session: SessionDep, storage: StorageDep
) -> DocumentOut:
    from app.repositories.business import BusinessProfileRepository

    order = _own_order(session, principal.tenant_id, order_id)
    lines = list(OrderItemRepository(session, principal.tenant_id).for_orders([order.id])[order.id])
    contact = ContactRepository(session, principal.tenant_id).get(order.contact_id)
    profile = BusinessProfileRepository(session, principal.tenant_id).current()
    try:
        document = DocumentService(session, principal.tenant_id, storage).make_summary(
            order,
            lines,
            business=(profile.business_name if profile and profile.business_name else ""),
            customer=short_name(contact, order) if contact else None,
            user_id=principal.user_id,
        )
    except DocumentError as exc:
        raise _error(exc) from exc
    session.commit()
    return _document_out(document)


@router.get(
    "/orders/{order_id}/documents/{document_id}/link",
    summary="A link to open a document, valid for ten minutes",
    responses=error_responses(NOT_FOUND, (503, "`storage_unavailable`"), *AUTH),
)
def document_link(
    order_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    storage: StorageDep,
) -> LinkOut:
    order = _own_order(session, principal.tenant_id, order_id)
    document = _own_document(session, principal.tenant_id, order, document_id)
    try:
        return LinkOut(url=DocumentService(session, principal.tenant_id, storage).link(document))
    except DocumentError as exc:
        raise _error(exc) from exc


@router.delete(
    "/orders/{order_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a document attached by mistake",
    responses=error_responses(NOT_FOUND, (409, "`issued`"), *AUTH),
)
def remove_document(
    order_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    storage: StorageDep,
) -> None:
    order = _own_order(session, principal.tenant_id, order_id)
    document = _own_document(session, principal.tenant_id, order, document_id)
    try:
        DocumentService(session, principal.tenant_id, storage).remove(document)
    except DocumentError as exc:
        raise _error(exc) from exc
    session.commit()


@router.post(
    "/orders/{order_id}/documents/{document_id}/void",
    summary="Void a document: the assistant stops sending it",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def void_document(
    order_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    storage: StorageDep,
) -> DocumentOut:
    order = _own_order(session, principal.tenant_id, order_id)
    document = _own_document(session, principal.tenant_id, order, document_id)
    DocumentService(session, principal.tenant_id, storage).void(document)
    session.commit()
    return _document_out(document)
