"""Orders and deliveries: the stages of the work, the comanda and the customer's pin (016).

What the operator does after confirming (prepare, ready, on its way, done), what the customer is
told and when, what the comanda shows, who can change what, and how a pin shared on WhatsApp
reaches the order it belongs to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_whatsapp_provider
from app.models.audit import AuditLog
from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.orders import Order
from app.orders.service import OrderService, pin_of
from app.providers.whatsapp import InboundLocation, YCloudProvider
from app.repositories.orders import OrderSettingsRepository
from app.workers.channel import OUTCOME_APPLIED, process_event
from tests.fixtures import load
from tests.integration.test_channel_events import _inbound, _store, _tenant_with_number
from tests.integration.test_orders_api import (
    ORDERS,
    FakeWhatsApp,
    operator_for,
    seed_order,
)
from tests.integration.test_team import _owner

D = Decimal


@pytest.fixture
def whatsapp(app: Any) -> FakeWhatsApp:
    provider = FakeWhatsApp()
    app.dependency_overrides[get_whatsapp_provider] = lambda: provider
    return provider


ADDRESS = {
    "nombres": "Ana Pérez",
    "telefono": "+51987654321",
    "direccion": "Av. Perú 123",
    "distrito": "San Miguel",
    "referencia": "Casa verde",
}


async def confirmed(
    api: AsyncClient,
    db: Session,
    headers: dict[str, str],
    tenant_id: str,
    *,
    tipo: str | None = "delivery",
    **seed: Any,
) -> Order:
    """An order the operator already confirmed, delivered or picked up."""
    order, _ = seed_order(db, tenant_id, **seed)
    order.tipo_entrega = tipo
    order.datos_cliente = dict(ADDRESS)
    db.commit()
    assert (await api.post(f"{ORDERS}/{order.id}/confirm", headers=headers)).status_code == 200
    db.expire_all()
    return order


async def stage(
    api: AsyncClient, headers: dict[str, str], order: Order, etapa: str
) -> dict[str, Any]:
    response = await api.post(f"{ORDERS}/{order.id}/stage", headers=headers, json={"etapa": etapa})
    return {"status": response.status_code, **response.json()}


class TestAccess:
    async def test_it_needs_a_login(self, api: AsyncClient) -> None:
        for method, url in [
            ("get", f"{ORDERS}/board"),
            ("get", f"{ORDERS}/settings"),
            ("put", f"{ORDERS}/settings"),
            ("post", f"{ORDERS}/{uuid.uuid4()}/stage"),
            ("post", f"{ORDERS}/{uuid.uuid4()}/refund-resolved"),
        ]:
            response = await getattr(api, method)(url)
            assert response.status_code == 401, (method, url)

    async def test_another_tenants_order_is_a_404(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        _, mine = await _owner(api, mail)
        theirs, their_headers = await _owner(api, mail)
        foreign = await confirmed(api, db, their_headers, theirs["tenant_id"])

        for url, body in [
            (f"{ORDERS}/{foreign.id}/stage", {"etapa": "preparando"}),
            (f"{ORDERS}/{foreign.id}/refund-resolved", None),
        ]:
            response = await api.post(url, headers=mine, **({"json": body} if body else {}))
            assert response.status_code == 404, url
            assert response.json()["error"] == "not_found"
        db.refresh(foreign)
        assert foreign.estado == "confirmado" and foreign.etapa is None

    async def test_an_operator_works_the_orders_but_only_a_manager_chooses_the_notices(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, owner, operator = await operator_for(api, mail, db)
        order = await confirmed(api, db, owner, account["tenant_id"])

        moved = await stage(api, operator, order, "preparando")
        read = await api.get(f"{ORDERS}/settings", headers=operator)
        write = await api.put(
            f"{ORDERS}/settings",
            headers=operator,
            json={"avisar_listo": False, "avisar_en_camino": False},
        )

        assert moved["status"] == 200 and moved["etapa"] == "preparando"
        assert read.status_code == 200
        assert write.status_code == 403


class TestTheStages:
    async def test_starting_the_work_moves_the_order_and_leaves_a_trace(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])

        moved = await stage(api, headers, order, "preparando")

        assert moved == {
            "status": 200,
            "estado": "en_curso",
            "etapa": "preparando",
            "aviso_cliente": None,
        }
        db.refresh(order)
        assert order.etapa_at is not None
        detail = (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()
        assert (detail["estado"], detail["etapa"]) == ("en_curso", "preparando")
        assert detail["etapa_desde"] is not None
        trail = [
            (entry["evento"], entry["estado"])
            for entry in detail["historial"]
            if entry["evento"] == "order.stage_changed"
        ]
        assert trail == [("order.stage_changed", "preparando")]
        audit = db.execute(
            select(AuditLog).where(
                AuditLog.action == "order.stage_changed",
                AuditLog.detail["order_id"].astext == str(order.id),
            )
        ).scalar_one()
        assert audit.detail["etapa_de"] is None and audit.detail["etapa_a"] == "preparando"

    async def test_the_first_stage_may_be_any_of_them(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        """A counter sale is ready the moment it is confirmed."""
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"], tipo="recojo")

        moved = await stage(api, headers, order, "listo")

        assert (moved["estado"], moved["etapa"]) == ("en_curso", "listo")

    async def test_a_mistaken_tap_can_be_undone(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        await stage(api, headers, order, "en_camino")

        back = await stage(api, headers, order, "preparando")

        assert back["status"] == 200 and back["etapa"] == "preparando"

    async def test_finishing_closes_the_clock_and_clears_the_stage(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        await stage(api, headers, order, "en_camino")

        done = await api.post(
            f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "completado"}
        )

        assert done.json()["estado"] == "completado"
        db.expire_all()
        finished = db.get(Order, order.id)
        assert finished is not None
        assert finished.etapa is None and finished.completado_at is not None
        detail = (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()
        assert detail["etapa"] is None and detail["completado"] is not None

    async def test_discarding_clears_the_stage(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        await stage(api, headers, order, "preparando")

        await api.post(
            f"{ORDERS}/{order.id}/status",
            headers=headers,
            json={"estado": "descartado", "motivo": "No hay insumos"},
        )

        db.expire_all()
        dropped = db.get(Order, order.id)
        assert dropped is not None and dropped.etapa is None

    async def test_starting_through_the_old_endpoint_is_the_first_stage(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])

        await api.post(f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "en_curso"})

        db.expire_all()
        started = db.get(Order, order.id)
        assert started is not None and started.etapa == "preparando"

    @pytest.mark.parametrize("estado", ["por_confirmar", "completado"])
    async def test_only_a_confirmed_order_has_stages(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp, estado: str
    ) -> None:
        account, headers = await _owner(api, mail)
        if estado == "por_confirmar":
            order, _ = seed_order(db, account["tenant_id"])
        else:
            order = await confirmed(api, db, headers, account["tenant_id"])
            await api.post(
                f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "en_curso"}
            )
            await api.post(
                f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": "completado"}
            )

        response = await stage(api, headers, order, "preparando")

        assert response["status"] == 409 and response["error"] == "invalid_transition"

    async def test_a_stage_that_does_not_exist_is_a_validation_error(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])

        response = await stage(api, headers, order, "entregado")

        assert response["status"] == 400 and response["error"] == "validation_error"

    async def test_a_pickup_is_never_on_its_way(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"], tipo="recojo")

        response = await stage(api, headers, order, "en_camino")

        assert response["status"] == 409 and response["error"] == "invalid_stage"
        db.refresh(order)
        assert order.estado == "confirmado" and order.etapa is None


class TestTellingTheCustomer:
    async def test_a_delivery_tells_the_customer_it_is_on_its_way_and_nothing_before(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        assert len(whatsapp.sent) == 1  # the payment confirmation

        preparing = await stage(api, headers, order, "preparando")
        ready = await stage(api, headers, order, "listo")
        away = await stage(api, headers, order, "en_camino")

        assert preparing["aviso_cliente"] is None and ready["aviso_cliente"] is None
        assert away["aviso_cliente"] == "enviado"
        [_, (_, text)] = whatsapp.sent
        assert f"#{order.numero}" in text and "en camino" in text

    async def test_a_pickup_tells_the_customer_it_is_ready(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"], tipo="recojo")

        ready = await stage(api, headers, order, "listo")

        assert ready["aviso_cliente"] == "enviado"
        [_, (_, text)] = whatsapp.sent
        assert f"#{order.numero}" in text and "recojas" in text

    async def test_a_stage_is_told_once_each_time_it_is_set_not_on_every_view(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        await stage(api, headers, order, "en_camino")

        await api.get(f"{ORDERS}/{order.id}", headers=headers)
        await api.get(f"{ORDERS}/board", headers=headers)

        assert len(whatsapp.sent) == 2  # the confirmation and the departure

    async def test_the_owner_can_turn_a_notice_off(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        saved = await api.put(
            f"{ORDERS}/settings",
            headers=headers,
            json={"avisar_listo": True, "avisar_en_camino": False},
        )

        away = await stage(api, headers, order, "en_camino")

        assert saved.json() == {"avisar_listo": True, "avisar_en_camino": False}
        assert (await api.get(f"{ORDERS}/settings", headers=headers)).json() == saved.json()
        assert away["aviso_cliente"] is None
        assert len(whatsapp.sent) == 1

    async def test_the_settings_start_with_both_notices_on(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, headers = await _owner(api, mail)

        body = (await api.get(f"{ORDERS}/settings", headers=headers)).json()

        assert body == {"avisar_listo": True, "avisar_en_camino": True}

    async def test_a_closed_window_saves_the_stage_and_does_not_write_to_the_customer(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"], window_open=False)

        away = await stage(api, headers, order, "en_camino")

        assert away["status"] == 200 and away["aviso_cliente"] == "ventana_cerrada"
        assert away["etapa"] == "en_camino"
        assert whatsapp.sent == []

    async def test_a_provider_failure_does_not_undo_the_stage(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        whatsapp.fail = True

        away = await stage(api, headers, order, "en_camino")

        assert away["status"] == 200 and away["etapa"] == "en_camino"

    async def test_the_owners_own_wording_is_used(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        edited = await api.put(
            "/api/v1/assistant/behavior",
            headers=headers,
            json={"mensajes_sistema": {"pedido_en_camino": "¡Sale tu #{numero}! 🛵"}},
        )
        assert edited.status_code == 200, edited.text

        await stage(api, headers, order, "en_camino")

        assert whatsapp.sent[-1][1] == f"¡Sale tu #{order.numero}! 🛵"

    async def test_the_two_notices_are_listed_for_the_owner_to_edit(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _owner(api, mail)

        body = (await api.get("/api/v1/assistant/behavior", headers=headers)).json()

        assert {"pedido_listo", "pedido_en_camino"} <= set(body["mensajes_sistema"])


class TestTheComanda:
    async def test_it_shows_the_work_of_the_day_and_nothing_else(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        tid = account["tenant_id"]
        waiting, _ = seed_order(db, tid)
        new = await confirmed(api, db, headers, tid)
        cooking = await confirmed(api, db, headers, tid)
        await stage(api, headers, cooking, "preparando")
        ready = await confirmed(api, db, headers, tid, tipo="recojo")
        await stage(api, headers, ready, "listo")
        finished = await confirmed(api, db, headers, tid)
        for step in ("en_curso", "completado"):
            await api.post(f"{ORDERS}/{finished.id}/status", headers=headers, json={"estado": step})
        dropped, _ = seed_order(db, tid, contra_entrega=True)
        await api.post(
            f"{ORDERS}/{dropped.id}/status", headers=headers, json={"estado": "descartado"}
        )
        seed_order(db, tid, sandbox=True)

        board = (await api.get(f"{ORDERS}/board", headers=headers)).json()

        shown = {item["id"]: (item["estado"], item["etapa"]) for item in board["ordenes"]}
        assert shown == {
            str(waiting.id): ("por_confirmar", None),
            str(new.id): ("confirmado", None),
            str(cooking.id): ("en_curso", "preparando"),
            str(ready.id): ("en_curso", "listo"),
            str(finished.id): ("completado", None),
        }

    async def test_a_card_carries_what_the_courier_and_the_kitchen_need(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"], contra_entrega=True)
        order.zona_envio = "San Miguel"
        order.ubicacion_entrega = {"lat": -12.07, "lng": -77.08, "en": "2026-10-22T10:00:00+00:00"}
        db.commit()

        [card] = (await api.get(f"{ORDERS}/board", headers=headers)).json()["ordenes"]

        assert card["numero"] == order.numero and card["tipo_entrega"] == "delivery"
        assert card["zona_envio"] == "San Miguel"
        assert card["items"] == [{"nombre": "Curso", "cantidad": 1, "selecciones": []}]
        assert (card["total"], card["moneda"]) == ("120.00", "PEN")
        assert card["cobrar_al_entregar"] is True
        assert card["entrega"] == {
            "receptor": "Ana Pérez",
            "telefono": "+51987654321",
            "direccion": "Av. Perú 123",
            "distrito": "San Miguel",
            "referencia": "Casa verde",
            "ubicacion_enlace": "https://www.google.com/maps?q=-12.070000,-77.080000",
        }

    async def test_an_order_with_nothing_to_deliver_has_no_delivery_block(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        await confirmed(api, db, headers, account["tenant_id"], tipo=None)

        [card] = (await api.get(f"{ORDERS}/board", headers=headers)).json()["ordenes"]

        assert card["entrega"] is None and card["cobrar_al_entregar"] is False

    async def test_the_one_that_waits_longest_is_first(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        first = await confirmed(api, db, headers, account["tenant_id"])
        second = await confirmed(api, db, headers, account["tenant_id"])
        first.confirmed_at = datetime.now(UTC) - timedelta(minutes=40)
        second.confirmed_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()

        board = (await api.get(f"{ORDERS}/board", headers=headers)).json()["ordenes"]

        assert [card["id"] for card in board] == [str(first.id), str(second.id)]

    async def test_yesterdays_finished_orders_are_not_on_todays_board(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        for step in ("en_curso", "completado"):
            await api.post(f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": step})
        db.expire_all()
        old = db.get(Order, order.id)
        assert old is not None
        old.completado_at = datetime.now(UTC) - timedelta(days=2)
        db.commit()

        board = (await api.get(f"{ORDERS}/board", headers=headers)).json()["ordenes"]

        assert board == []

    async def test_it_never_shows_another_businesses_orders(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        mine, my_headers = await _owner(api, mail)
        theirs, their_headers = await _owner(api, mail)
        await confirmed(api, db, their_headers, theirs["tenant_id"])

        board = (await api.get(f"{ORDERS}/board", headers=my_headers)).json()

        assert board == {"ordenes": []}
        assert mine["tenant_id"] != theirs["tenant_id"]

    async def test_the_list_says_the_stage_and_the_way_of_delivery(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        await stage(api, headers, order, "en_camino")

        [item] = (await api.get(ORDERS, headers=headers)).json()["items"]

        assert (item["estado"], item["etapa"], item["tipo_entrega"]) == (
            "en_curso",
            "en_camino",
            "delivery",
        )


class TestTheRefundOwed:
    async def test_the_business_marks_it_paid_back(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        await api.post(
            f"{ORDERS}/{order.id}/status",
            headers=headers,
            json={"estado": "descartado", "motivo": "El cliente se arrepintió"},
        )

        resolved = await api.post(f"{ORDERS}/{order.id}/refund-resolved", headers=headers)

        assert resolved.status_code == 200, resolved.text
        assert resolved.json() == {
            "estado": "descartado",
            "aviso_cliente": None,
            "devolucion_pendiente": False,
        }
        detail = (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()
        assert detail["devolucion_pendiente"] is False
        audit = db.execute(
            select(AuditLog).where(
                AuditLog.action == "order.refund_resolved",
                AuditLog.detail["order_id"].astext == str(order.id),
            )
        ).scalar_one()
        assert audit.detail["order_id"] == str(order.id)

    async def test_an_order_that_owes_nothing_cannot_be_marked(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])

        response = await api.post(f"{ORDERS}/{order.id}/refund-resolved", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"] == "no_refund_owed"


class TestTheAverageTime:
    async def test_the_summary_averages_confirmed_to_finished(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        empty = (await api.get(f"{ORDERS}/summary", headers=headers)).json()
        now = datetime.now(UTC)
        for minutes in (30, 90):
            order = await confirmed(api, db, headers, account["tenant_id"])
            for step in ("en_curso", "completado"):
                await api.post(
                    f"{ORDERS}/{order.id}/status", headers=headers, json={"estado": step}
                )
            db.expire_all()
            done = db.get(Order, order.id)
            assert done is not None
            done.confirmed_at = now - timedelta(minutes=minutes)
            done.completado_at = now
            db.commit()

        summary = (await api.get(f"{ORDERS}/summary", headers=headers)).json()

        assert empty["tiempo_medio_entrega_min"] is None
        assert summary["tiempo_medio_entrega_min"] == 60


class TestSettingsRepository:
    def test_two_calls_make_one_row(self, db: Session) -> None:
        tenant, _ = _tenant_with_number(db)

        first = OrderSettingsRepository(db, tenant.id).ensure()
        second = OrderSettingsRepository(db, tenant.id).ensure()

        assert first.tenant_id == second.tenant_id == tenant.id
        assert (first.avisar_listo, first.avisar_en_camino) == (True, True)


# --------------------------------------------------------------------- the pin


def _world(db: Session) -> tuple[Any, str, Contact, Conversation]:
    """A business, its number, and a customer who already wrote once."""
    tenant, phone = _tenant_with_number(db)
    process_event(db, _store(db, _inbound(phone)), provider=YCloudProvider())
    contact = db.execute(select(Contact).where(Contact.tenant_id == tenant.id)).scalar_one()
    conversation = db.execute(
        select(Conversation).where(Conversation.tenant_id == tenant.id)
    ).scalar_one()
    return tenant, phone, contact, conversation


def _location_event(phone: str, **overrides: Any) -> dict[str, Any]:
    payload = load("ycloud/inbound_location.json")
    payload["whatsappInboundMessage"]["to"] = phone
    payload["whatsappInboundMessage"]["id"] = f"wamid.{uuid.uuid4().hex}"
    payload["whatsappInboundMessage"]["location"].update(overrides)
    return payload


def _draft(db: Session, tenant_id: uuid.UUID, conversation: Conversation) -> Order:
    product = Product(tenant_id=tenant_id, name="Pizza", price=D("30.00"))
    db.add(product)
    db.flush()
    service = OrderService(db, tenant_id)
    order = service.open_draft(
        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
    )
    service.add_product(order, product)
    db.commit()
    return order


class TestThePinTheCustomerShares:
    def test_it_is_stored_as_words_and_keeps_its_coordinates_in_the_payload(
        self, db: Session
    ) -> None:
        tenant, phone, _, conversation = _world(db)

        outcome = process_event(db, _store(db, _location_event(phone)), provider=YCloudProvider())

        assert outcome.outcome == OUTCOME_APPLIED
        message = db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id, Message.msg_type == "location"
            )
        ).scalar_one()
        assert message.content == (
            "[Ubicación compartida por WhatsApp] Plaza Mayor · Jr. de la Unión s/n, Lima"
        )
        assert message.payload == {
            "location": {
                "lat": -12.046374,
                "lng": -77.042793,
                "nombre": "Plaza Mayor",
                "direccion": "Jr. de la Unión s/n, Lima",
            }
        }
        assert tenant.id == conversation.tenant_id

    def test_it_goes_to_the_order_the_conversation_is_working_on(self, db: Session) -> None:
        tenant, phone, _, conversation = _world(db)
        order = _draft(db, tenant.id, conversation)

        process_event(db, _store(db, _location_event(phone)), provider=YCloudProvider())

        db.expire_all()
        kept = db.get(Order, order.id)
        assert kept is not None and kept.ubicacion_entrega is not None
        assert kept.ubicacion_entrega["lat"] == pytest.approx(-12.046374)
        assert kept.ubicacion_entrega["nombre"] == "Plaza Mayor"

    def test_a_pin_before_the_order_is_adopted_when_it_is_submitted(self, db: Session) -> None:
        tenant, phone, contact, conversation = _world(db)
        process_event(db, _store(db, _location_event(phone)), provider=YCloudProvider())
        order = _draft(db, tenant.id, conversation)
        assert order.ubicacion_entrega is None

        OrderService(db, tenant.id).submit(order, contact)

        assert order.ubicacion_entrega is not None
        assert order.ubicacion_entrega["direccion"] == "Jr. de la Unión s/n, Lima"

    def test_an_old_pin_is_not_adopted(self, db: Session) -> None:
        tenant, phone, contact, conversation = _world(db)
        process_event(db, _store(db, _location_event(phone)), provider=YCloudProvider())
        pin = db.execute(select(Message).where(Message.msg_type == "location")).scalar_one()
        pin.created_at = datetime.now(UTC) - timedelta(hours=13)
        db.commit()
        order = _draft(db, tenant.id, conversation)

        OrderService(db, tenant.id).submit(order, contact)

        assert order.ubicacion_entrega is None

    def test_a_pin_never_reaches_a_finished_order(self, db: Session) -> None:
        tenant, phone, contact, conversation = _world(db)
        order = _draft(db, tenant.id, conversation)
        service = OrderService(db, tenant.id)
        service.submit(order, contact)
        service.transition(order, "confirmado")
        service.transition(order, "en_curso")
        service.transition(order, "completado")
        db.commit()

        process_event(db, _store(db, _location_event(phone)), provider=YCloudProvider())

        db.expire_all()
        closed = db.get(Order, order.id)
        assert closed is not None and closed.ubicacion_entrega is None

    def test_a_pin_the_provider_could_not_read_changes_nothing_for_the_order(
        self, db: Session
    ) -> None:
        tenant, phone, _, conversation = _world(db)
        order = _draft(db, tenant.id, conversation)

        process_event(
            db,
            _store(db, _location_event(phone, latitude=250.0)),
            provider=YCloudProvider(),
        )

        db.expire_all()
        kept = db.get(Order, order.id)
        assert kept is not None and kept.ubicacion_entrega is None
        message = db.execute(select(Message).where(Message.msg_type == "location")).scalar_one()
        assert message.content == "[ubicacion]"

    def test_the_service_refuses_to_pin_a_closed_order(self, db: Session) -> None:
        tenant, _, _, conversation = _world(db)
        order = _draft(db, tenant.id, conversation)
        order.estado = "descartado"
        pin = pin_of(InboundLocation(lat=-12.0, lng=-77.0), datetime.now(UTC))

        assert OrderService(db, tenant.id).attach_location(order, pin) is False
        assert order.ubicacion_entrega is None

    async def test_the_operator_gets_a_link_to_open_it(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])
        order.ubicacion_entrega = pin_of(
            InboundLocation(lat=-12.046374, lng=-77.042793, nombre="Plaza Mayor"),
            datetime(2026, 10, 22, 15, 0, tzinfo=UTC),
        )
        db.commit()

        pin = (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()["ubicacion"]

        assert pin["enlace"] == "https://www.google.com/maps?q=-12.046374,-77.042793"
        assert (pin["lat"], pin["lng"], pin["nombre"]) == (-12.046374, -77.042793, "Plaza Mayor")
        assert pin["compartida"].startswith("2026-10-22T15:00")

    async def test_an_order_without_a_pin_says_so(
        self, api: AsyncClient, mail: Any, db: Session, whatsapp: FakeWhatsApp
    ) -> None:
        account, headers = await _owner(api, mail)
        order = await confirmed(api, db, headers, account["tenant_id"])

        detail = (await api.get(f"{ORDERS}/{order.id}", headers=headers)).json()

        assert detail["ubicacion"] is None
