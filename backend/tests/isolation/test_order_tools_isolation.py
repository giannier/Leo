"""`gestionar_pedido` stays inside its tenant (Feature 004, TASK-426).

The ids in a tool call come from the model, which got them from a customer: a
product id, a promotion id, a conversation id of another business must read as
"does not exist", never as a hit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.tools.orders import gestionar_pedido
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import Product
from app.models.conversation import Conversation
from app.models.sales_config import Promotion
from app.orders.service import OrderService
from app.process.templates import TEMPLATES
from app.providers.llm import MockLLMProvider
from tests.isolation.conftest import TenantFixture


def _context(db: Session, fixture: TenantFixture, **overrides: Any) -> ToolContext:
    values: dict[str, Any] = {
        "session": db,
        "tenant_id": fixture.tenant_id,
        "llm": MockLLMProvider(),
        "conversation_id": fixture.conversation_id,
        "sandbox": True,
    }
    values.update(overrides)
    return ToolContext(**values)


async def _act(context: ToolContext, **arguments: Any) -> ToolResult:
    return await gestionar_pedido(context, arguments)


@pytest.fixture
def with_contacts(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    for fixture in (tenant_a, tenant_b):
        conversation = db.get(Conversation, fixture.conversation_id)
        assert conversation is not None
        conversation.contact_id = fixture.contact_id
    db.flush()


@pytest.mark.usefixtures("with_contacts")
class TestAnotherTenantsIds:
    async def test_a_foreign_product_is_not_added(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        result = await _act(
            _context(db, tenant_a), accion="agregar", producto_id=str(tenant_b.product_id)
        )

        assert result.status == "no_disponible"
        assert result.data["pedido"] is None

    async def test_a_foreign_conversation_is_not_found(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        context = _context(db, tenant_a, conversation_id=tenant_b.conversation_id)

        result = await _act(context, accion="agregar", producto_id=str(tenant_a.product_id))

        assert result.status == "error"

    async def test_the_order_of_another_business_is_never_shown(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        conversation_b = db.get(Conversation, tenant_b.conversation_id)
        assert conversation_b is not None
        service_b = OrderService(db, tenant_b.tenant_id)
        order_b = service_b.open_draft(
            conversation_b, moneda="PEN", modalidad_pago="previo", is_sandbox=False
        )
        product_b = db.get(Product, tenant_b.product_id)
        assert product_b is not None
        service_b.add_product(order_b, product_b)

        mine = await _act(_context(db, tenant_a), accion="ver")
        theirs = await _act(
            _context(db, tenant_a, conversation_id=tenant_b.conversation_id), accion="ver"
        )

        assert mine.data == {"pedido": None}
        assert theirs.status == "error"

    async def test_a_foreign_promotion_cannot_be_offered(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        coupon = Promotion(
            tenant_id=tenant_b.tenant_id,
            nombre="Cupón de B",
            uso="cierre",
            tipo="porcentaje",
            valor=Decimal("50"),
            vigencia_horas=24,
        )
        db.add(coupon)
        db.flush()
        context = _context(db, tenant_a)

        result = await _act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        assert result.status == "no_disponible"
        assert "promos_ofrecidas" not in context.facts_patch

    async def test_a_foreign_product_cannot_be_chosen_as_a_selection(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        package = Product(
            tenant_id=tenant_a.tenant_id,
            name="Combo A",
            price=Decimal("50.00"),
            bundle_size=2,
        )
        db.add(package)
        db.flush()
        context = _context(db, tenant_a)
        await _act(context, accion="agregar", producto_id=str(package.id))

        result = await _act(context, accion="elegir", producto_id=str(tenant_b.product_id))

        assert result.status == "error"
        unknown = await _act(context, accion="elegir", seleccion="Product B")
        assert unknown.status == "no_disponible"


@pytest.mark.usefixtures("with_contacts")
class TestCustomerDataStaysInsideTheTenant:
    async def test_data_go_to_the_contact_of_this_tenant_only(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        from app.agent.tools.customer import registrar_datos_cliente
        from app.models.channel import Contact

        options = TEMPLATES["productos"].resolve()
        result = await registrar_datos_cliente(
            _context(db, tenant_a, options=options), {"nombres": "Ana"}
        )

        assert result.status == "hecho"
        contact_a = db.get(Contact, tenant_a.contact_id)
        contact_b = db.get(Contact, tenant_b.contact_id)
        assert contact_a is not None and contact_b is not None
        assert contact_a.custom_data == {"nombres": "Ana"}
        assert contact_b.custom_data == {}

    async def test_a_foreign_conversation_saves_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        from app.agent.tools.customer import registrar_datos_cliente
        from app.models.channel import Contact

        context = _context(db, tenant_a, conversation_id=tenant_b.conversation_id)

        result = await registrar_datos_cliente(context, {"nombres": "Intruso"})

        assert result.status == "error"
        contact_b = db.get(Contact, tenant_b.contact_id)
        assert contact_b is not None and contact_b.custom_data == {}


@pytest.mark.usefixtures("with_contacts")
class TestChangingLinesWithForeignIds:
    """TASK-446: removing or resizing with another business's product id."""

    @pytest.mark.parametrize("accion", ["quitar", "cantidad"])
    async def test_a_foreign_product_changes_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, accion: str
    ) -> None:
        context = _context(db, tenant_a)
        await _act(context, accion="agregar", producto_id=str(tenant_a.product_id))

        result = await _act(
            _context(db, tenant_a), accion=accion, producto_id=str(tenant_b.product_id), cantidad=5
        )

        assert result.status in ("no_disponible", "error")
        order = result.data.get("pedido") or {}
        items = order.get("items", [])
        assert [(i["nombre"], i["cantidad"]) for i in items] in ([], [("Product A", 1)])
