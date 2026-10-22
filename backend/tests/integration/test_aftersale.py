"""Returns and the documents of an order: the tools, the rules and the panel (019, phase G)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersale.documents import DocumentService
from app.aftersale.pdf_writer import SimplePdf
from app.aftersale.returns import ReturnError, ReturnService
from app.agent.tools.aftersale import enviar_documento, solicitar_devolucion
from app.agent.tools.postsale import consultar_pedidos
from app.agent.tools.registry import ToolContext
from app.models.aftersale import OrderDocument, OrderReturn, OrderReturnEvent
from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Message
from app.models.media import MediaAsset
from app.providers.llm import MockLLMProvider
from app.providers.storage.mock import MockStorageProvider
from app.repositories.aftersale import DocumentRepository, ReturnRepository
from app.repositories.orders import OrderItemRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.services.turn_outbound import SandboxOutbound
from tests.integration.test_crm_leads import _user
from tests.integration.test_orders_api import (  # noqa: F401 - fixtures
    FakeWhatsApp,
    operator_for,
    outbound,
    seed_order,
    storage,
    whatsapp,
)
from tests.integration.test_postsale_tool import _order
from tests.integration.test_team import _owner
from tests.integration.test_turn_pipeline import _conversation, _tenant

RETURNS = "/api/v1/tenant/returns"
ORDERS = "/api/v1/tenant/orders"


class Notices:
    """What the business would be told."""

    def __init__(self) -> None:
        self.returns: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []

    def return_requested(self, order: Any, *, numero: int, tipo: str, motivo: str) -> None:
        self.returns.append(
            {"pedido": order.numero, "numero": numero, "tipo": tipo, "motivo": motivo}
        )

    def document_requested(self, order: Any, *, tipo: str | None) -> None:
        self.documents.append({"pedido": order.numero, "tipo": tipo})


def _context(db: Session, tenant: Any, conversation: Any, **extra: Any) -> ToolContext:
    return ToolContext(
        session=db,
        tenant_id=tenant.id,
        llm=MockLLMProvider(),
        conversation_id=conversation.id,
        notices=extra.pop("notices", Notices()),
        adapter=extra.pop("adapter", SandboxOutbound(db, tenant.id, conversation.id)),
        **extra,
    )


def _shop(
    db: Session, *, estado: str = "completado", name: str = "Polo básico"
) -> tuple[Any, Any, Any, Any]:
    tenant = _tenant(db, template="productos")
    conversation = _conversation(db, tenant, channel="whatsapp")
    polo = Product(tenant_id=tenant.id, name=name, price=Decimal("35"))
    db.add(polo)
    db.flush()
    order = _order(db, tenant.id, conversation, estado, lines=((name, 2, polo),))
    return tenant, conversation, order, polo


def _policy(db: Session, tenant: Any, **data: Any) -> None:
    BusinessInfoRepository(db, tenant.id).upsert(
        "devoluciones",
        {"acepta_cambios": True, "acepta_devoluciones": True, "plazo_dias": 7, **data},
    )


class TestRegisteringARequest:
    async def test_it_registers_what_the_customer_asked_and_tells_the_team(
        self, db: Session
    ) -> None:
        tenant, conversation, order, _ = _shop(db)
        notices = Notices()

        result = await solicitar_devolucion(
            _context(db, tenant, conversation, notices=notices),
            {"tipo": "cambio", "motivo": "defectuoso", "detalle": "Llegó con la costura abierta"},
        )

        (ret,) = db.execute(select(OrderReturn)).scalars().all()
        assert result.status == "hecho" and "NO prometas" in (result.decir or "")
        assert (ret.tipo, ret.motivo, ret.estado, ret.created_by) == (
            "cambio",
            "defectuoso",
            "solicitada",
            "agent",
        )
        assert ret.order_id == order.id and ret.numero == 1
        assert notices.returns == [
            {"pedido": order.numero, "numero": 1, "tipo": "cambio", "motivo": "defectuoso"}
        ]
        (event,) = db.execute(select(OrderReturnEvent)).scalars().all()
        assert (event.desde, event.hacia, event.actor) == (None, "solicitada", "agent")

    async def test_asking_twice_finds_the_first_one(self, db: Session) -> None:
        tenant, conversation, _, _ = _shop(db)
        notices = Notices()
        context = _context(db, tenant, conversation, notices=notices)
        arguments = {"tipo": "cambio", "motivo": "defectuoso", "detalle": "No sirve"}

        await solicitar_devolucion(context, arguments)
        again = await solicitar_devolucion(context, arguments)

        assert again.status == "omitido" and "#1" in (again.decir or "")
        assert len(db.execute(select(OrderReturn)).scalars().all()) == 1
        assert len(notices.returns) == 1  # the team is told once

    async def test_with_several_orders_it_asks_which_one(self, db: Session) -> None:
        tenant, conversation, first, polo = _shop(db)
        second = _order(db, tenant.id, conversation, "confirmado", lines=(("Gorra", 1, None),))
        context = _context(db, tenant, conversation)

        vague = await solicitar_devolucion(
            context, {"tipo": "devolucion", "motivo": "defectuoso", "detalle": "x"}
        )
        chosen = await solicitar_devolucion(
            context,
            {"pedido": first.numero, "tipo": "devolucion", "motivo": "defectuoso", "detalle": "x"},
        )

        assert vague.status == "omitido"
        assert f"#{first.numero}" in (vague.decir or "") and f"#{second.numero}" in (
            vague.decir or ""
        )
        assert chosen.status == "hecho"
        (ret,) = db.execute(select(OrderReturn)).scalars().all()
        assert ret.order_id == first.id

    async def test_nobody_reaches_an_order_that_is_not_theirs(self, db: Session) -> None:
        tenant, conversation, _, _ = _shop(db)
        stranger = _conversation(db, tenant, channel="whatsapp")
        theirs = _order(db, tenant.id, stranger, "completado", lines=(("Gorra", 1, None),))

        result = await solicitar_devolucion(
            _context(db, tenant, conversation),
            {"pedido": theirs.numero, "tipo": "devolucion", "motivo": "otro", "detalle": "x"},
        )

        assert result.status == "omitido"  # asks again, with the customer's own orders only
        assert f"#{theirs.numero}" not in (result.decir or "")
        assert db.execute(select(OrderReturn)).first() is None

    async def test_without_a_confirmed_order_there_is_nothing_to_return(self, db: Session) -> None:
        tenant, conversation, _, _ = _shop(db, estado="por_confirmar")

        result = await solicitar_devolucion(
            _context(db, tenant, conversation),
            {"tipo": "devolucion", "motivo": "otro", "detalle": "x"},
        )

        assert result.status == "no_disponible"
        assert db.execute(select(OrderReturn)).first() is None

    async def test_a_change_of_mind_after_the_window_is_not_registered_but_a_defect_is(
        self, db: Session
    ) -> None:
        tenant, conversation, order, _ = _shop(db)
        _policy(db, tenant, plazo_dias=7)
        order.confirmed_at = datetime.now(UTC) - timedelta(days=30)
        db.flush()
        context = _context(db, tenant, conversation)

        regret = await solicitar_devolucion(
            context, {"tipo": "devolucion", "motivo": "no_conforme", "detalle": "No me gustó"}
        )
        defect = await solicitar_devolucion(
            context, {"tipo": "devolucion", "motivo": "defectuoso", "detalle": "Viene roto"}
        )

        assert regret.status == "no_disponible" and "7 días" in (regret.decir or "")
        assert "derivar_a_humano" in (regret.decir or "")
        assert defect.status == "hecho"

    async def test_a_business_that_takes_no_returns_still_registers_a_warranty(
        self, db: Session
    ) -> None:
        tenant, conversation, _, _ = _shop(db)
        _policy(db, tenant, acepta_devoluciones=False)
        context = _context(db, tenant, conversation)

        refund = await solicitar_devolucion(
            context, {"tipo": "devolucion", "motivo": "otro", "detalle": "Ya no lo quiero"}
        )
        warranty = await solicitar_devolucion(
            context, {"tipo": "garantia", "motivo": "defectuoso", "detalle": "Se dañó"}
        )

        assert refund.status == "no_disponible" and "no hace devoluciones" in (refund.decir or "")
        assert warranty.status == "hecho"

    async def test_the_photos_sent_a_moment_ago_are_attached_as_evidence(self, db: Session) -> None:
        tenant, conversation, _, _ = _shop(db)
        old = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            msg_type="image",
            content="[imagen]",
            created_at=datetime.now(UTC) - timedelta(days=2),
        )
        fresh = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            msg_type="image",
            content="[imagen]",
        )
        db.add_all([old, fresh])
        db.flush()

        await solicitar_devolucion(
            _context(db, tenant, conversation),
            {"tipo": "reclamo", "motivo": "defectuoso", "detalle": "Mira cómo llegó"},
        )

        (ret,) = db.execute(select(OrderReturn)).scalars().all()
        assert ret.evidencia == [{"message_id": str(fresh.id)}]

    async def test_a_product_that_was_not_in_the_order_is_refused(self, db: Session) -> None:
        tenant, conversation, order, _ = _shop(db)
        lines = list(OrderItemRepository(db, tenant.id).for_orders([order.id])[order.id])

        with pytest.raises(ReturnError) as caught:
            ReturnService(db, tenant.id).open(
                order,
                tipo="devolucion",
                motivo="otro",
                detalle="",
                items=[{"nombre": "Zapatillas"}],
                lines=lines,
                actor="user",
            )

        assert caught.value.code == "item_not_in_order"


class TestDeciding:
    def _open(self, db: Session) -> tuple[Any, OrderReturn]:
        tenant, _, order, _ = _shop(db)
        lines = list(OrderItemRepository(db, tenant.id).for_orders([order.id])[order.id])
        opened = ReturnService(db, tenant.id).open(
            order,
            tipo="devolucion",
            motivo="defectuoso",
            detalle="",
            items=[],
            lines=lines,
            actor="agent",
        )
        return tenant, opened.ret

    def test_approving_needs_to_say_what_happens_next(self, db: Session) -> None:
        tenant, ret = self._open(db)

        with pytest.raises(ReturnError) as caught:
            ReturnService(db, tenant.id).decide(ret, to="aprobada", user_id=_user(db))

        assert caught.value.code == "resolution_required"
        assert ret.estado == "solicitada"

    def test_a_request_goes_through_its_states_and_every_step_leaves_an_event(
        self, db: Session
    ) -> None:
        tenant, ret = self._open(db)
        service = ReturnService(db, tenant.id)
        person = _user(db)

        service.decide(ret, to="aprobada", user_id=person, resolucion="Te devolvemos por Yape")
        service.decide(ret, to="resuelta", user_id=person)

        steps = [
            (e.desde, e.hacia)
            for e in db.execute(select(OrderReturnEvent).order_by(OrderReturnEvent.created_at))
            .scalars()
            .all()
        ]
        assert steps == [(None, "solicitada"), ("solicitada", "aprobada"), ("aprobada", "resuelta")]
        assert ret.resolucion == "Te devolvemos por Yape" and ret.resuelta_at is not None

    def test_what_is_over_cannot_be_decided_again(self, db: Session) -> None:
        tenant, ret = self._open(db)
        service = ReturnService(db, tenant.id)
        service.decide(ret, to="rechazada", user_id=_user(db), resolucion="Fuera de plazo")

        with pytest.raises(ReturnError) as caught:
            service.decide(ret, to="aprobada", user_id=_user(db), resolucion="Mejor sí")

        assert caught.value.code == "invalid_transition"

    def test_a_closed_request_makes_room_for_a_new_one_of_the_same_kind(self, db: Session) -> None:
        tenant, ret = self._open(db)
        service = ReturnService(db, tenant.id)
        service.decide(ret, to="rechazada", user_id=_user(db), resolucion="No aplica")
        from app.models.orders import Order

        lines = list(OrderItemRepository(db, tenant.id).for_orders([ret.order_id])[ret.order_id])
        order = db.get(Order, ret.order_id)
        assert order is not None
        again = service.open(
            order,
            tipo="devolucion",
            motivo="defectuoso",
            detalle="otra vez",
            items=[],
            lines=lines,
            actor="user",
        )

        assert again.created and again.ret.numero == 2


class TestTheDocumentsOfAnOrder:
    def _pdf(self) -> bytes:
        pdf = SimplePdf()
        pdf.text("Boleta de venta electrónica B001-123")
        return pdf.render()

    def _attached(self, db: Session, tenant: Any, order: Any, **extra: Any) -> OrderDocument:
        return DocumentService(db, tenant.id, MockStorageProvider()).attach(
            order, self._pdf(), "boleta.pdf", tipo="boleta", serie="B001", numero="123", **extra
        )

    async def test_the_assistant_sends_the_document_of_the_customers_own_order(
        self, db: Session
    ) -> None:
        tenant, conversation, order, _ = _shop(db)
        document = self._attached(db, tenant, order)
        db.flush()

        result = await enviar_documento(_context(db, tenant, conversation), {"tipo": "boleta"})

        assert result.status == "hecho" and "Boleta B001-123" in (result.decir or "")
        (sent,) = outbound(db, conversation.id)
        assert sent.msg_type == "document" and sent.payload["asset_id"] == str(document.id)
        assert document.enviado_at is not None

    async def test_a_document_is_never_a_library_file(self, db: Session) -> None:
        tenant, conversation, order, _ = _shop(db)
        self._attached(db, tenant, order)
        db.flush()

        await enviar_documento(_context(db, tenant, conversation), {})

        # Sending borrowed the shape of a library asset but never stored one: it could be named
        # by a key otherwise, and a customer's invoice must not be reachable that way.
        assert db.execute(select(MediaAsset)).first() is None

    async def test_somebody_else_never_gets_it(self, db: Session) -> None:
        tenant, _, order, _ = _shop(db)
        self._attached(db, tenant, order)
        db.flush()
        stranger = _conversation(db, tenant, channel="whatsapp")
        notices = Notices()

        result = await enviar_documento(
            _context(db, tenant, stranger, notices=notices), {"tipo": "boleta"}
        )

        assert result.status == "no_disponible"
        assert outbound(db, stranger.id) == []

    async def test_a_document_that_is_not_attached_is_not_invented_and_the_team_is_told(
        self, db: Session
    ) -> None:
        tenant, conversation, order, _ = _shop(db)
        notices = Notices()

        result = await enviar_documento(
            _context(db, tenant, conversation, notices=notices), {"tipo": "factura"}
        )

        assert result.status == "no_disponible" and "todavía no está disponible" in (
            result.decir or ""
        )
        assert notices.documents == [{"pedido": order.numero, "tipo": "factura"}]
        assert outbound(db, conversation.id) == []

    async def test_a_voided_document_is_not_offered(self, db: Session) -> None:
        tenant, conversation, order, _ = _shop(db)
        document = self._attached(db, tenant, order)
        DocumentService(db, tenant.id, MockStorageProvider()).void(document)

        result = await enviar_documento(_context(db, tenant, conversation), {"tipo": "boleta"})

        assert result.status == "no_disponible"

    async def test_the_orders_tool_says_which_documents_and_requests_there_are(
        self, db: Session
    ) -> None:
        tenant, conversation, order, _ = _shop(db)
        self._attached(db, tenant, order)
        context = _context(db, tenant, conversation)
        await solicitar_devolucion(
            context, {"tipo": "cambio", "motivo": "equivocado", "detalle": "Pedí talla M"}
        )

        result = await consultar_pedidos(context, {})

        text = result.render()
        assert "Boleta B001-123" in text and "enviar_documento" in text
        assert "Solicitud de cambio #1: en revisión por el equipo" in text

    def test_only_a_pdf_or_an_image_is_accepted(self, db: Session) -> None:
        from app.aftersale.documents import DocumentError

        tenant, _, order, _ = _shop(db)
        service = DocumentService(db, tenant.id, MockStorageProvider())

        with pytest.raises(DocumentError) as caught:
            service.attach(order, b"MZ\x90\x00 an executable", "boleta.pdf", tipo="boleta")

        assert caught.value.code == "unsupported_file"
        assert db.execute(select(OrderDocument)).first() is None

    def test_the_summary_is_a_pdf_that_says_it_is_not_a_receipt(self, db: Session) -> None:
        import io

        from pypdf import PdfReader

        tenant, _, order, _ = _shop(db)
        storage = MockStorageProvider()
        lines = list(OrderItemRepository(db, tenant.id).for_orders([order.id])[order.id])

        document = DocumentService(db, tenant.id, storage).make_summary(
            order, lines, business="Tienda Ñ", customer="Ana", user_id=None
        )

        text = PdfReader(io.BytesIO(storage.get(document.storage_key))).pages[0].extract_text()
        assert document.origen == "generado" and document.tipo == "otro"
        assert "Resumen del pedido" in text and "Polo básico" in text
        assert "no es un comprobante de pago" in text

    def test_an_electronic_document_is_voided_never_deleted(self, db: Session) -> None:
        from app.aftersale.documents import DocumentError

        tenant, _, order, _ = _shop(db)
        service = DocumentService(db, tenant.id, MockStorageProvider())
        document = service.attach(order, self._pdf(), "f.pdf", tipo="factura", origen="electronico")

        with pytest.raises(DocumentError) as caught:
            service.remove(document)

        assert caught.value.code == "issued"


class TestThePanel:
    async def _confirmed(self, db: Session, tenant_id: str) -> Any:
        order, _ = seed_order(db, tenant_id)
        order.estado = "confirmado"
        order.confirmed_at = datetime.now(UTC)
        db.commit()
        return order

    async def test_it_needs_a_login(self, api: AsyncClient) -> None:
        some = uuid.uuid4()
        for method, url in [
            ("get", RETURNS),
            ("get", f"{RETURNS}/{some}"),
            ("post", RETURNS),
            ("post", f"{RETURNS}/{some}/decision"),
            ("get", f"{ORDERS}/{some}/documents"),
            ("post", f"{ORDERS}/{some}/documents"),
        ]:
            assert (await getattr(api, method)(url)).status_code == 401, (method, url)

    async def test_the_team_registers_a_request_and_decides_it_and_the_customer_reads_it(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, _, operator = await operator_for(api, mail, db)
        order = await self._confirmed(db, account["tenant_id"])

        created = await api.post(
            RETURNS,
            headers=operator,
            json={
                "order_id": str(order.id),
                "tipo": "devolucion",
                "motivo": "defectuoso",
                "detalle": "Roto",
            },
        )
        listing = await api.get(RETURNS, headers=operator)
        duplicate = await api.post(
            RETURNS,
            headers=operator,
            json={"order_id": str(order.id), "tipo": "devolucion", "motivo": "otro"},
        )
        needs_text = await api.post(
            f"{RETURNS}/{created.json()['id']}/decision", headers=operator, json={"a": "aprobada"}
        )
        decided = await api.post(
            f"{RETURNS}/{created.json()['id']}/decision",
            headers=operator,
            json={"a": "aprobada", "resolucion": "Te devolvemos por Yape en 48 horas."},
        )
        again = await api.post(
            f"{RETURNS}/{created.json()['id']}/decision",
            headers=operator,
            json={"a": "rechazada", "resolucion": "no"},
        )

        assert created.status_code == 201, created.text
        assert listing.json()["total"] == 1 and listing.json()["pendientes"] == 1
        of_order = await api.get(f"{RETURNS}?pedido_id={order.id}", headers=operator)
        of_other = await api.get(f"{RETURNS}?pedido_id={uuid.uuid4()}", headers=operator)
        assert of_order.json()["total"] == 1 and of_other.json()["total"] == 0
        assert duplicate.status_code == 409 and duplicate.json()["error"] == "already_open"
        assert needs_text.status_code == 422 and needs_text.json()["error"] == "resolution_required"
        assert decided.status_code == 200, decided.text
        assert decided.json()["aviso_cliente"] == "enviado"
        assert decided.json()["solicitud"]["estado"] == "aprobada"
        assert [e["hacia"] for e in decided.json()["solicitud"]["eventos"]] == [
            "solicitada",
            "aprobada",
        ]
        assert any("Te devolvemos por Yape" in body for _, body in whatsapp.sent)
        assert again.status_code == 409 and again.json()["error"] == "invalid_transition"

    async def test_an_unconfirmed_order_cannot_have_a_request(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])  # waiting for confirmation

        response = await api.post(
            RETURNS,
            headers=headers,
            json={"order_id": str(order.id), "tipo": "cambio", "motivo": "otro"},
        )

        assert response.status_code == 409 and response.json()["error"] == "order_not_sold"

    async def test_another_tenants_request_and_documents_are_a_404(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        storage: MockStorageProvider,  # noqa: F811
    ) -> None:
        mine, my_headers = await _owner(api, mail)
        theirs, their_headers = await _owner(api, mail)
        order = await self._confirmed(db, theirs["tenant_id"])
        created = await api.post(
            RETURNS,
            headers=their_headers,
            json={"order_id": str(order.id), "tipo": "cambio", "motivo": "otro"},
        )
        pdf = SimplePdf().render()

        seen = await api.get(f"{RETURNS}/{created.json()['id']}", headers=my_headers)
        decided = await api.post(
            f"{RETURNS}/{created.json()['id']}/decision",
            headers=my_headers,
            json={"a": "cancelada"},
        )
        documents = await api.get(f"{ORDERS}/{order.id}/documents", headers=my_headers)
        uploaded = await api.post(
            f"{ORDERS}/{order.id}/documents",
            headers=my_headers,
            files={"file": ("b.pdf", pdf, "application/pdf")},
            data={"tipo": "boleta"},
        )
        listing = await api.get(RETURNS, headers=my_headers)

        assert {
            seen.status_code,
            decided.status_code,
            documents.status_code,
            uploaded.status_code,
        } == {404}
        assert listing.json()["total"] == 0

    async def test_attach_open_make_a_summary_and_remove(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        storage: MockStorageProvider,  # noqa: F811
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await self._confirmed(db, account["tenant_id"])
        pdf = SimplePdf()
        pdf.text("Boleta")

        uploaded = await api.post(
            f"{ORDERS}/{order.id}/documents",
            headers=headers,
            files={"file": ("boleta.pdf", pdf.render(), "application/pdf")},
            data={"tipo": "boleta", "serie": "B001", "numero": "55"},
        )
        rejected = await api.post(
            f"{ORDERS}/{order.id}/documents",
            headers=headers,
            files={"file": ("virus.pdf", b"MZ not a document", "application/pdf")},
            data={"tipo": "boleta"},
        )
        summary = await api.post(f"{ORDERS}/{order.id}/documents/summary", headers=headers)
        listed = await api.get(f"{ORDERS}/{order.id}/documents", headers=headers)
        link = await api.get(
            f"{ORDERS}/{order.id}/documents/{uploaded.json()['id']}/link", headers=headers
        )
        voided = await api.post(
            f"{ORDERS}/{order.id}/documents/{uploaded.json()['id']}/void", headers=headers
        )
        removed = await api.delete(
            f"{ORDERS}/{order.id}/documents/{summary.json()['id']}", headers=headers
        )

        assert uploaded.status_code == 201, uploaded.text
        assert (
            uploaded.json()["nombre"] == "Boleta B001-55" and uploaded.json()["origen"] == "subido"
        )
        assert rejected.status_code == 422 and rejected.json()["error"] == "unsupported_file"
        assert summary.status_code == 201 and summary.json()["origen"] == "generado"
        assert {d["tipo"] for d in listed.json()["items"]} == {"boleta", "otro"}
        assert link.status_code == 200 and link.json()["url"]
        assert voided.json()["estado"] == "anulado"
        assert removed.status_code == 204
        remaining = DocumentRepository(db, uuid.UUID(account["tenant_id"])).of_order(order.id)
        assert [d.tipo for d in remaining] == ["boleta"]

    async def test_every_member_of_the_team_reads_the_list(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, _, operator = await operator_for(api, mail, db)
        order = await self._confirmed(db, account["tenant_id"])
        repo = ReturnRepository(db, uuid.UUID(account["tenant_id"]))
        assert repo.count_waiting() == 0 and order is not None

        listing = await api.get(RETURNS, headers=operator)

        assert listing.status_code == 200 and listing.json() == {
            "items": [],
            "total": 0,
            "pendientes": 0,
        }
        assert Contact is not None
