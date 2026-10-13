"""The orders API and the notices to the customer (Feature 004, TASK-430).

The verification of the task: API tests by role, a closed 24-hour window answering
`ventana_cerrada` without calling the provider, another tenant's order giving 404, and
a double confirmation giving 409. Plus what the contract says each decision does: the
receipt, the order, the audit entry, the stage with its event, and the message the
customer reads.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_storage_provider, get_whatsapp_provider
from app.core.crypto import encrypt_secret
from app.models.audit import AuditLog
from app.models.business import AgentConfig, Product
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationStageEvent, FunnelCode
from app.models.orders import Order, PaymentProof
from app.models.whatsapp import WhatsAppNumber
from app.orders.service import OrderService
from app.process.recompute import recompute_stage
from app.process.state import StateRepo
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.providers.storage.mock import MockStorageProvider
from app.providers.whatsapp.base import (
    NumberCredentials,
    Recipient,
    SendResult,
    WhatsAppTransientError,
)
from app.repositories.orders import PaymentProofRepository
from tests.integration.test_team import (
    AUTH,
    PASSWORD,
    TEAM,
    _link_token,
    _owner,
    _set_seats,
    _unique_email,
)

ORDERS = "/api/v1/tenant/orders"
D = Decimal
SHOP = TEMPLATES["productos"]


class FakeWhatsApp:
    """Records what would have gone to the customer."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    async def send_text(
        self, credentials: NumberCredentials, *, sender: str, to: Recipient, body: str
    ) -> SendResult:
        if self.fail:
            raise WhatsAppTransientError("gateway down")
        self.sent.append((to.phone or "", body))
        return SendResult(provider_message_id=f"wamid.{uuid.uuid4().hex}", status="sent")


@pytest.fixture
def whatsapp(app: Any) -> FakeWhatsApp:
    provider = FakeWhatsApp()
    app.dependency_overrides[get_whatsapp_provider] = lambda: provider
    return provider


@pytest.fixture
def storage(app: Any) -> MockStorageProvider:
    provider = MockStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: provider
    return provider


def seed_order(
    db: Session,
    tenant_id: str,
    *,
    sandbox: bool = False,
    window_open: bool = True,
    contra_entrega: bool = False,
    name: str = "Ana Pérez Soto",
    phone: str | None = None,
    price: str = "120.00",
    submit: bool = True,
) -> tuple[Order, PaymentProof | None]:
    """A customer who paid and waits: order `por_confirmar`, a receipt, stage VERIFICACION."""
    tid = uuid.UUID(tenant_id)
    if not db.get(AgentConfig, tid):
        db.add(AgentConfig(tenant_id=tid, agent_name="Leo"))
    if not db.execute(select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == tid)).first():
        db.add(
            WhatsAppNumber(
                tenant_id=tid,
                phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
                display_name="Tienda",
                provider="ycloud",
                status="connected",
                credentials_enc=encrypt_secret(
                    json.dumps({"waba_id": "1", "phone_number_id": "2", "api_key": "yc_key_1"})
                ),
            )
        )
    contact = Contact(
        tenant_id=tid,
        phone_e164=None if sandbox else (phone or f"+5198{uuid.uuid4().int % 10**7:07d}"),
        external_user_id=f"sandbox:{uuid.uuid4()}" if sandbox else None,
        profile_name="Ana WA",
        custom_data={"nombres": name},
        last_inbound_at=datetime.now(UTC)
        - (timedelta(hours=1) if window_open else timedelta(days=2)),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(
        tenant_id=tid,
        channel="sandbox" if sandbox else "whatsapp",
        contact_id=contact.id,
        status="open",
    )
    db.add(conversation)
    product = Product(tenant_id=tid, name="Curso", price=D(price), es_digital=True)
    db.add(product)
    db.flush()
    service = OrderService(db, tid)
    order = service.open_draft(
        conversation,
        moneda="PEN",
        modalidad_pago="contra_entrega" if contra_entrega else "previo",
        is_sandbox=sandbox,
    )
    service.add_product(order, product)

    states = StateRepo(db, tid)
    state = states.get_or_create(conversation, SHOP)
    states.save(
        conversation.id,
        stage=Stage.INICIO,
        funnel=FunnelCode(state.funnel_code),
        facts_patch={"saludo_enviado": True},
        reason="turno",
        expected_version=state.version,
    )
    proof = None
    if submit:
        if not contra_entrega:
            proof = PaymentProofRepository(db, tid).add(
                order_id=order.id,
                conversation_id=conversation.id,
                storage_key=f"tenants/{tid}/proofs/{uuid.uuid4()}.jpg",
                monto=D(price),
                moneda="PEN",
                codigo_operacion="OP-1",
                entidad="Yape",
                confianza=D("0.90"),
                observaciones=[],
            )
        service.submit(order, contact)
        recompute_stage(db, tid, conversation, reason="comprobante")
    db.commit()
    return order, proof


async def operator_for(
    api: AsyncClient, mail: Any, db: Session
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """(account, owner headers, operator headers) of one tenant."""
    account, owner = await _owner(api, mail)
    _set_seats(db, account["tenant_id"], 10)
    await api.post(
        f"{TEAM}/invitations",
        headers=owner,
        json={"email": _unique_email("bruno"), "role": "operator"},
    )
    joined = await api.post(
        f"{AUTH}/invitations/accept",
        json={"token": _link_token(mail.last), "full_name": "Bruno", "password": PASSWORD},
    )
    assert joined.status_code == 200
    return account, owner, {"Authorization": f"Bearer {joined.json()['access_token']}"}


def events(db: Session, conversation_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = (
        db.execute(
            select(ConversationStageEvent)
            .where(ConversationStageEvent.conversation_id == conversation_id)
            .order_by(ConversationStageEvent.created_at, ConversationStageEvent.id)
        )
        .scalars()
        .all()
    )
    return [(row.to_stage, row.reason) for row in rows]


def outbound(db: Session, conversation_id: uuid.UUID) -> list[Message]:
    return list(
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.direction == "outbound")
            .order_by(Message.created_at)
        )
        .scalars()
        .all()
    )


class TestAccess:
    async def test_it_needs_a_login(self, api: AsyncClient) -> None:
        for method, url in [
            ("get", ORDERS),
            ("get", f"{ORDERS}/summary"),
            ("get", f"{ORDERS}/{uuid.uuid4()}"),
            ("post", f"{ORDERS}/{uuid.uuid4()}/confirm"),
            ("post", f"{ORDERS}/{uuid.uuid4()}/reject"),
            ("post", f"{ORDERS}/{uuid.uuid4()}/status"),
        ]:
            response = await getattr(api, method)(url)
            assert response.status_code == 401, (method, url)

    async def test_an_operator_can_read_and_decide(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, _, operator = await operator_for(api, mail, db)
        order, _ = seed_order(db, account["tenant_id"])

        listing = await api.get(ORDERS, headers=operator)
        decided = await api.post(f"{ORDERS}/{order.id}/confirm", headers=operator)

        assert listing.status_code == 200 and listing.json()["total"] == 1
        assert decided.status_code == 200, decided.text
        assert decided.json() == {"estado": "confirmado", "aviso_cliente": "enviado"}

    async def test_another_tenants_order_is_a_404_everywhere(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        mine, my_headers = await _owner(api, mail)
        theirs, _ = await _owner(api, mail)
        foreign, proof = seed_order(db, theirs["tenant_id"])
        assert proof is not None

        urls = [
            ("get", f"{ORDERS}/{foreign.id}", None),
            ("get", f"{ORDERS}/{foreign.id}/proofs/{proof.id}/image", None),
            ("post", f"{ORDERS}/{foreign.id}/confirm", None),
            ("post", f"{ORDERS}/{foreign.id}/reject", {"motivo": "no corresponde"}),
            ("post", f"{ORDERS}/{foreign.id}/status", {"estado": "en_curso"}),
        ]
        for method, url, body in urls:
            response = await getattr(api, method)(
                url, headers=my_headers, **({"json": body} if body else {})
            )
            assert response.status_code == 404, (method, url, response.text)
            assert response.json()["error"] == "not_found"
        db.refresh(foreign)
        assert foreign.estado == "por_confirmar"
        assert whatsapp.sent == []
        assert (await api.get(ORDERS, headers=my_headers)).json()["total"] == 0
        assert mine["tenant_id"] != theirs["tenant_id"]


class TestListAndSummary:
    async def test_the_list_masks_the_phone_and_describes_the_order(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], phone="+51987654628")

        body = (await api.get(ORDERS, headers=headers)).json()

        [item] = body["items"]
        assert item["id"] == str(order.id) and item["numero"] == order.numero
        assert item["estado"] == "por_confirmar"
        assert item["cliente"] == {"nombre": "Ana P.", "telefono": "+51 9•• ••• 628"}
        assert (item["total"], item["moneda"]) == ("120.00", "PEN")
        assert item["items_resumen"] == "Curso"
        assert item["tiene_comprobante"] is True and item["observaciones"] == []
        assert body["page"] == 1

    async def test_filters_search_and_pagination(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        tid = account["tenant_id"]
        first, _ = seed_order(db, tid, name="Carla Ruiz", phone="+51977777777")
        second, _ = seed_order(db, tid, name="Diego Paz", phone="+51988888888", submit=False)
        seed_order(db, tid, sandbox=True)

        everything = (await api.get(ORDERS, headers=headers)).json()
        assert everything["total"] == 2  # the simulator's order is hidden
        assert (await api.get(f"{ORDERS}?sandbox=true", headers=headers)).json()["total"] == 3

        waiting = (await api.get(f"{ORDERS}?estado=por_confirmar", headers=headers)).json()
        assert [i["id"] for i in waiting["items"]] == [str(first.id)]
        both = (
            await api.get(f"{ORDERS}?estado=por_confirmar&estado=borrador", headers=headers)
        ).json()
        assert both["total"] == 2

        for query, expected in [
            (f"q=%23{second.numero}", second),
            ("q=carla", first),
            ("q=888888", second),
            ("q=%25", None),
        ]:
            found = (await api.get(f"{ORDERS}?{query}", headers=headers)).json()
            assert [i["id"] for i in found["items"]] == ([str(expected.id)] if expected else []), (
                query
            )

        today = datetime.now(UTC).date().isoformat()
        assert (await api.get(f"{ORDERS}?desde={today}&hasta={today}", headers=headers)).json()[
            "total"
        ] == 2
        assert (await api.get(f"{ORDERS}?hasta=2020-01-01", headers=headers)).json()["total"] == 0

        paged = (await api.get(f"{ORDERS}?page_size=1&page=2", headers=headers)).json()
        assert paged["total"] == 2 and len(paged["items"]) == 1 and paged["page"] == 2
        assert (await api.get(f"{ORDERS}?page_size=51", headers=headers)).status_code == 400
        unknown = await api.get(f"{ORDERS}?estado=perdido", headers=headers)
        assert unknown.status_code == 400 and unknown.json()["error"] == "validation_error"

    async def test_the_summary_counts_sales_not_drafts_nor_the_simulator(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        tid = account["tenant_id"]
        sold_a, _ = seed_order(db, tid, price="100.00")
        sold_b, _ = seed_order(db, tid, price="200.00")
        seed_order(db, tid, price="50.00")  # still waiting
        seed_order(db, tid, submit=False)  # a draft
        sandbox_sale, _ = seed_order(db, tid, sandbox=True, price="999.00")
        for sold in (sold_a, sold_b, sandbox_sale):
            assert (
                await api.post(f"{ORDERS}/{sold.id}/confirm", headers=headers)
            ).status_code == 200

        body = (await api.get(f"{ORDERS}/summary?periodo=7d", headers=headers)).json()

        assert body["conversiones"] == 2
        assert (body["valor_total"], body["ticket_promedio"], body["moneda"]) == (
            "300.00",
            "150.00",
            "PEN",
        )
        assert body["por_estado"]["confirmado"] == 2
        assert body["por_estado"]["por_confirmar"] == 1
        assert body["por_estado"]["borrador"] == 1
        assert body["pendientes_revision"] == 1
        today = (await api.get(f"{ORDERS}/summary", headers=headers)).json()
        assert today["conversiones"] == 2
        empty = await api.get(f"{ORDERS}/summary?periodo=30d", headers=(await _owner(api, mail))[1])
        assert empty.json()["conversiones"] == 0 and empty.json()["ticket_promedio"] == "0.00"
        assert (await api.get(f"{ORDERS}/summary?periodo=1y", headers=headers)).status_code == 400


class TestDetail:
    async def test_it_shows_everything_the_operator_needs(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        order, proof = seed_order(db, account["tenant_id"], phone="+51987654628")
        assert proof is not None

        body = (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()

        assert body["estado"] == "por_confirmar" and body["numero"] == order.numero
        assert body["items"][0]["nombre"] == "Curso"
        assert (body["subtotal"], body["total"]) == ("120.00", "120.00")
        # Complete for a member of the tenant (the list masks it).
        assert body["datos_cliente"]["telefono"] == "+51987654628"
        assert body["datos_cliente"]["nombres"] == "Ana Pérez Soto"
        [shown] = body["comprobantes"]
        assert shown["id"] == str(proof.id) and shown["codigo_operacion"] == "OP-1"
        assert "storage_key" not in shown and "url" not in shown
        assert body["ventana_abierta"] is True
        assert body["conversation_id"] == str(order.conversation_id)
        assert [h["evento"] for h in body["historial"]] == ["creado", "comprobante_recibido"]

    async def test_a_closed_window_is_reported(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], window_open=False)

        assert (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()[
            "ventana_abierta"
        ] is False

    async def test_the_receipt_photo_is_a_ten_minute_link(
        self, api: AsyncClient, mail: Any, db: Session, storage: MockStorageProvider
    ) -> None:
        account, headers = await _owner(api, mail)
        order, proof = seed_order(db, account["tenant_id"])
        assert proof is not None
        storage.put(proof.storage_key, b"jpg", content_type="image/jpeg")

        response = await api.get(f"{ORDERS}/{order.id}/proofs/{proof.id}/image", headers=headers)

        body = response.json()
        assert response.status_code == 200
        assert body["url"].endswith("expires_in=600")
        assert datetime.fromisoformat(body["expires_at"]) > datetime.now(UTC) + timedelta(minutes=9)

    async def test_a_proof_of_another_order_is_not_found(
        self, api: AsyncClient, mail: Any, db: Session, storage: MockStorageProvider
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])
        _, other_proof = seed_order(db, account["tenant_id"])
        assert other_proof is not None

        response = await api.get(
            f"{ORDERS}/{order.id}/proofs/{other_proof.id}/image", headers=headers
        )

        assert response.status_code == 404


class TestConfirm:
    async def test_it_does_everything_the_contract_says(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, proof = seed_order(db, account["tenant_id"], phone="+51987654628")
        assert proof is not None

        response = await api.post(
            f"{ORDERS}/{order.id}/confirm",
            headers=headers,
            json={"siguiente_paso": "Te enviamos tu acceso hoy"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"estado": "confirmado", "aviso_cliente": "enviado"}
        db.refresh(order)
        db.refresh(proof)
        assert order.estado == "confirmado" and order.confirmed_at is not None
        assert order.confirmed_by is not None
        assert order.siguiente_paso == "Te enviamos tu acceso hoy"
        assert proof.estado == "aprobado" and proof.reviewed_at is not None
        # The audit entry carries who and what, never the customer's data.
        [entry] = db.execute(select(AuditLog).where(AuditLog.action == "order.confirmed")).scalars()
        assert entry.tenant_id == uuid.UUID(account["tenant_id"])
        assert entry.detail["order_id"] == str(order.id)
        assert (entry.detail["de"], entry.detail["a"]) == ("por_confirmar", "confirmado")
        assert "Pérez" not in json.dumps(entry.detail)
        # The stage follows, with its event.
        assert events(db, order.conversation_id) == [
            ("VERIFICACION", "comprobante"),
            ("CERRADO", "operador:confirmar"),
        ]
        # The customer reads the system message after the commit.
        assert whatsapp.sent == [
            (
                "+51987654628",
                f"¡Ana, tu pago fue confirmado! ✅ Pedido #{order.numero}. "
                "Te enviamos tu acceso hoy",
            )
        ]
        [message] = outbound(db, order.conversation_id)
        assert message.author == "system" and message.delivery_status == "sent"

    async def test_a_double_confirmation_is_a_409_and_tells_the_customer_once(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])

        first = await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)
        second = await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"] == "already_reviewed"
        assert len(whatsapp.sent) == 1

    async def test_an_order_that_is_not_waiting_cannot_be_confirmed(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        draft, _ = seed_order(db, account["tenant_id"], submit=False)

        response = await api.post(f"{ORDERS}/{draft.id}/confirm", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_transition"
        assert whatsapp.sent == []

    async def test_a_closed_window_saves_the_decision_and_never_calls_the_provider(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], window_open=False)

        response = await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"estado": "confirmado", "aviso_cliente": "ventana_cerrada"}
        assert whatsapp.sent == []
        assert outbound(db, order.conversation_id) == []
        db.refresh(order)
        assert order.estado == "confirmado"

    async def test_the_simulator_gets_the_notice_as_a_stored_message_only(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], sandbox=True, window_open=False)

        response = await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        assert response.json()["aviso_cliente"] == "enviado"
        assert whatsapp.sent == []
        [message] = outbound(db, order.conversation_id)
        assert message.author == "system" and message.delivery_status is None
        assert "tu pago fue confirmado" in message.content

    async def test_the_simulator_reads_the_notice_as_an_asynchronous_outbound(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], sandbox=True)

        await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)
        polled = await api.get(
            f"/api/v1/tenant/sandbox/conversations/{order.conversation_id}/messages",
            headers=headers,
        )

        assert polled.status_code == 200, polled.text
        [message] = polled.json()["items"]
        assert message["direction"] == "outbound"
        assert message["saliente"]["tipo"] == "texto"
        assert "tu pago fue confirmado" in message["saliente"]["texto"]

    async def test_a_provider_failure_does_not_undo_the_decision(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])
        whatsapp.fail = True

        response = await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"estado": "confirmado", "aviso_cliente": "no_enviado"}
        db.refresh(order)
        assert order.estado == "confirmado"
        [message] = outbound(db, order.conversation_id)
        assert message.delivery_status == "failed"

    async def test_the_owners_own_wording_and_a_missing_name_and_step(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], name=" ")
        config = db.get(AgentConfig, uuid.UUID(account["tenant_id"]))
        assert config is not None
        config.system_messages = {
            "pago_confirmado": "¡Listo, {nombre_cliente}! Pedido {numero}. {siguiente_paso}"
        }
        db.commit()

        await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        [(_, body)] = whatsapp.sent
        assert "{" not in body
        assert body == f"¡Listo! Pedido {order.numero}."


class TestReject:
    async def test_a_rejected_receipt_keeps_the_payment_rejected_until_something_changes(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, proof = seed_order(db, account["tenant_id"], phone="+51987654628")
        assert proof is not None

        response = await api.post(
            f"{ORDERS}/{order.id}/reject",
            headers=headers,
            json={"motivo": "El monto no coincide con el total"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"estado": "borrador", "aviso_cliente": "enviado"}
        db.refresh(order)
        db.refresh(proof)
        assert order.estado == "borrador"
        assert order.rechazo_vigente is True
        assert order.motivo_rechazo == "El monto no coincide con el total"
        assert proof.estado == "rechazado"
        assert proof.motivo_rechazo == "El monto no coincide con el total"
        # The stage was already VERIFICACION (waiting) and still is (rejected): no new event.
        assert events(db, order.conversation_id) == [("VERIFICACION", "comprobante")]
        state = StateRepo(db, uuid.UUID(account["tenant_id"])).get(order.conversation_id)
        assert state is not None and state.stage_key == "VERIFICACION"
        [(_, body)] = whatsapp.sent
        assert body == (
            "Ana, revisamos tu comprobante y hay un detalle: El monto no coincide con el total. "
            "¿Nos ayudas a corregirlo?"
        )
        [entry] = db.execute(select(AuditLog).where(AuditLog.action == "order.rejected")).scalars()
        assert entry.detail["proof_id"] == str(proof.id)

    async def test_cash_on_delivery_goes_back_to_checkout_with_the_reason(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, proof = seed_order(db, account["tenant_id"], contra_entrega=True)
        assert proof is None
        assert events(db, order.conversation_id)[-1][0] == "VERIFICACION"

        response = await api.post(
            f"{ORDERS}/{order.id}/reject",
            headers=headers,
            json={"motivo": "No llegamos a esa zona"},
        )

        assert response.status_code == 200
        db.refresh(order)
        assert order.estado == "borrador" and order.rechazo_vigente is False
        assert order.motivo_rechazo == "No llegamos a esa zona"
        assert events(db, order.conversation_id)[-1] == ("COBRO", "operador:rechazar")
        [(_, body)] = whatsapp.sent
        assert body.startswith(
            f"Ana, revisamos tu pedido #{order.numero} y hay un detalle: No llegamos"
        )

    @pytest.mark.parametrize("motivo", ["", "malo", "x" * 301, "   "])
    async def test_the_reason_is_required_and_bounded(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp, motivo: str
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])

        response = await api.post(
            f"{ORDERS}/{order.id}/reject", headers=headers, json={"motivo": motivo}
        )

        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"
        db.refresh(order)
        assert order.estado == "por_confirmar"

    async def test_a_confirmed_order_cannot_be_rejected(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])
        await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        response = await api.post(
            f"{ORDERS}/{order.id}/reject", headers=headers, json={"motivo": "Ya no corresponde"}
        )

        assert response.status_code == 409
        assert response.json()["error"] == "already_reviewed"

    async def test_with_a_closed_window_the_customer_is_not_written_to(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"], window_open=False)

        response = await api.post(
            f"{ORDERS}/{order.id}/reject", headers=headers, json={"motivo": "Falta el código"}
        )

        assert response.json() == {"estado": "borrador", "aviso_cliente": "ventana_cerrada"}
        assert whatsapp.sent == []


class TestStatus:
    async def test_a_confirmed_order_moves_along_to_completed(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])
        await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)

        started = await api.post(
            f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "en_curso"}
        )
        done = await api.post(
            f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "completado"}
        )

        assert started.json() == {"estado": "en_curso"}
        assert done.json() == {"estado": "completado"}
        # Still a closed sale: no stage change, so no extra event.
        assert events(db, order.conversation_id) == [
            ("VERIFICACION", "comprobante"),
            ("CERRADO", "operador:confirmar"),
        ]
        assert len(whatsapp.sent) == 1  # only the confirmation

    @pytest.mark.parametrize(
        ("setup", "target"),
        [("completed", "descartado"), ("waiting", "en_curso"), ("waiting", "completado")],
    )
    async def test_a_move_the_matrix_forbids_is_a_409(
        self,
        api: AsyncClient,
        mail: Any,
        db: Session,
        whatsapp: FakeWhatsApp,
        setup: str,
        target: str,
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])
        if setup == "completed":
            await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)
            for step in ("en_curso", "completado"):
                await api.post(
                    f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": step}
                )

        response = await api.post(
            f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": target}
        )

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_transition"

    async def test_discarding_a_waiting_order_recomputes_the_stage(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])

        response = await api.post(
            f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "descartado"}
        )

        assert response.json() == {"estado": "descartado"}
        assert events(db, order.conversation_id)[-1][1] == "operador:estado"
        assert whatsapp.sent == []

    async def test_a_state_the_operator_cannot_set_is_rejected(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, headers = await _owner(api, mail)
        order, _ = seed_order(db, account["tenant_id"])

        response = await api.post(
            f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "confirmado"}
        )

        assert response.status_code == 400
