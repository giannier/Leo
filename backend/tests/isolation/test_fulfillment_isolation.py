"""One business never reaches another's comanda, notice settings or the pin of its customer (016).

The pin is a customer's location and the comanda is a list of addresses: the cases hand tenant A
tenant B's real ids and expect nothing back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.conversation import Message
from app.models.orders import Order
from app.orders.service import OrderService, pin_of
from app.providers.whatsapp.base import InboundLocation
from app.repositories.base import TenantScopeError
from app.repositories.conversation import MessageRepository
from app.repositories.orders import OrderRepository, OrderSettingsRepository
from tests.isolation.conftest import TenantFixture

PIN = InboundLocation(lat=-12.0464, lng=-77.0428, nombre="Plaza Mayor")


def _order(db: Session, fixture: TenantFixture, estado: str = "en_curso") -> Order:
    order = OrderRepository(db, fixture.tenant_id).add_numbered(
        conversation_id=fixture.conversation_id,
        contact_id=fixture.contact_id,
        moneda="PEN",
        estado=estado,
        tipo_entrega="delivery",
    )
    order.etapa = "preparando" if estado == "en_curso" else None
    order.etapa_at = datetime.now(UTC)
    return order


class TestTheComanda:
    def test_a_tenant_sees_only_its_own_orders_on_the_board(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine, theirs = _order(db, tenant_a), _order(db, tenant_b)
        db.flush()

        board = OrderRepository(db, tenant_a.tenant_id).board(
            finished_since=datetime.now(UTC) - timedelta(days=1)
        )

        assert [order.id for order, _ in board] == [mine.id]
        assert theirs.id not in {order.id for order, _ in board}

    def test_the_average_time_counts_only_its_own_orders(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        now = datetime.now(UTC)
        for fixture, minutes in ((tenant_a, 10), (tenant_b, 500)):
            order = _order(db, fixture, "completado")
            order.confirmed_at = now - timedelta(minutes=minutes)
            order.completado_at = now
        db.flush()

        since = now - timedelta(days=1)

        assert OrderRepository(db, tenant_a.tenant_id).fulfillment_minutes(since=since) == 10
        assert OrderRepository(db, tenant_b.tenant_id).fulfillment_minutes(since=since) == 500


class TestTheNoticeSettings:
    def test_each_business_has_its_own_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = OrderSettingsRepository(db, tenant_a.tenant_id).ensure()
        mine.avisar_en_camino = False
        theirs = OrderSettingsRepository(db, tenant_b.tenant_id).ensure()
        db.flush()

        assert mine.tenant_id == tenant_a.tenant_id and theirs.tenant_id == tenant_b.tenant_id
        assert theirs.avisar_en_camino is True
        assert OrderSettingsRepository(db, tenant_a.tenant_id).current() is mine


class TestThePin:
    def test_a_service_refuses_to_pin_another_businesses_order(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        foreign = _order(db, tenant_b)
        db.flush()

        with pytest.raises(TenantScopeError):
            OrderService(db, tenant_a.tenant_id).attach_location(
                foreign, pin_of(PIN, datetime.now(UTC))
            )
        with pytest.raises(TenantScopeError):
            OrderService(db, tenant_a.tenant_id).set_stage(foreign, "listo")
        assert foreign.ubicacion_entrega is None and foreign.etapa == "preparando"

    def test_a_tenant_never_finds_the_pin_of_another_businesss_conversation(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        db.add(
            Message(
                tenant_id=tenant_b.tenant_id,
                conversation_id=tenant_b.conversation_id,
                direction="inbound",
                author="customer",
                content="[Ubicación compartida por WhatsApp]",
                msg_type="location",
                payload={"location": {"lat": -12.0, "lng": -77.0}},
            )
        )
        db.flush()
        since = datetime.now(UTC) - timedelta(hours=1)

        assert (
            MessageRepository(db, tenant_a.tenant_id).latest_location(
                tenant_b.conversation_id, since=since
            )
            is None
        )
        assert (
            MessageRepository(db, tenant_b.tenant_id).latest_location(
                tenant_b.conversation_id, since=since
            )
            is not None
        )
