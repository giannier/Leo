"""After the sale: the assistant answers for orders already made (feature 019, phase G)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.agent.tools.postsale import consultar_pedidos
from app.agent.tools.registry import ToolContext
from app.models.business import Product
from app.models.orders import OrderItem
from app.providers.llm import MockLLMProvider
from app.repositories.orders import OrderRepository
from tests.integration.test_turn_pipeline import _conversation, _tenant


def _order(
    db: Session,
    tenant_id,
    conversation,
    estado: str,
    *,
    total: str = "95.00",
    sandbox: bool = False,
    next_step: str | None = None,
    etapa: str | None = None,
    tipo_entrega: str = "delivery",
    lines: tuple[tuple[str, int, Product | None], ...] = (),
):  # type: ignore[no-untyped-def]
    order = OrderRepository(db, tenant_id).add_numbered(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        moneda="PEN",
        subtotal=Decimal(total),
        total=Decimal(total),
        estado=estado,
        is_sandbox=sandbox,
        etapa=etapa,
        tipo_entrega=tipo_entrega,
        zona_envio="Miraflores" if tipo_entrega == "delivery" else None,
        siguiente_paso=next_step,
        confirmed_at=datetime.now(UTC) if estado != "borrador" else None,
    )
    for name, quantity, product in lines:
        db.add(
            OrderItem(
                tenant_id=tenant_id,
                order_id=order.id,
                product_id=product.id if product else None,
                nombre=name,
                precio_unitario=Decimal("35.00"),
                cantidad=quantity,
                subtotal=Decimal("35.00") * quantity,
            )
        )
    db.flush()
    return order


def _context(db: Session, tenant, conversation, **extra) -> ToolContext:  # type: ignore[no-untyped-def]
    return ToolContext(
        session=db,
        tenant_id=tenant.id,
        llm=MockLLMProvider(),
        conversation_id=conversation.id,
        **extra,
    )


class TestTheCustomerAsksAboutAnOrder:
    @pytest.mark.parametrize(
        ("etapa", "tipo", "says"),
        [
            ("preparando", "delivery", "en preparación"),
            (None, "delivery", "en preparación"),
            ("listo", "delivery", "listo, a punto de salir"),
            ("listo", "recojo", "listo para recoger en el local"),
            ("en_camino", "delivery", "en camino"),
        ],
    )
    async def test_it_says_the_exact_stage_the_business_recorded(
        self, db: Session, etapa: str | None, tipo: str, says: str
    ) -> None:
        """Feature 016: not "en preparación o en camino" for everything in progress."""
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        _order(db, tenant.id, conversation, "en_curso", etapa=etapa, tipo_entrega=tipo)

        result = await consultar_pedidos(_context(db, tenant, conversation), {})

        assert result.texto is not None and f"{says}. Total" in result.texto

    async def test_the_state_the_lines_and_what_the_business_wrote(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        polo = Product(tenant_id=tenant.id, name="Polo básico", price=Decimal("35"))
        db.add(polo)
        db.flush()
        _order(
            db,
            tenant.id,
            conversation,
            "en_curso",
            next_step="Sale mañana por la mañana",
            lines=(("Polo básico", 2, polo), ("Gorra libre", 1, None)),
        )

        result = await consultar_pedidos(_context(db, tenant, conversation), {})

        assert result.status == "hecho" and result.texto is not None
        assert "en preparación" in result.texto
        assert "2 × Polo básico" in result.texto and f"producto_id: {polo.id}" in result.texto
        assert (
            "1 × Gorra libre" in result.texto
            and "producto_id" not in result.texto.split("Gorra libre")[1].split("\n")[0]
        )
        assert "Total PEN 95.00" in result.texto and "delivery a Miraflores" in result.texto
        assert "Sale mañana por la mañana" in result.texto
        assert "no prometas fechas" in result.texto

    async def test_a_draft_is_not_an_order_and_the_newest_come_first(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        _order(db, tenant.id, conversation, "completado", total="10.00")
        _order(db, tenant.id, conversation, "confirmado", total="20.00")
        _order(db, tenant.id, conversation, "borrador", total="30.00")

        text = (await consultar_pedidos(_context(db, tenant, conversation), {})).texto or ""

        assert "30.00" not in text
        assert text.index("20.00") < text.index("10.00")

    async def test_at_most_three_and_none_means_say_so(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        empty = await consultar_pedidos(_context(db, tenant, conversation), {})
        for _ in range(5):
            _order(db, tenant.id, conversation, "completado")

        text = (await consultar_pedidos(_context(db, tenant, conversation), {})).texto or ""

        assert empty.status == "no_disponible" and "no tiene pedidos" in (empty.decir or "")
        assert text.count("Pedido #") == 3

    async def test_nobody_sees_somebody_else_s_orders_and_the_simulator_sees_its_own(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        mine = _conversation(db, tenant, channel="whatsapp")
        theirs = _conversation(db, tenant, channel="whatsapp")
        simulator = _conversation(db, tenant, channel="sandbox")
        _order(db, tenant.id, theirs, "confirmado", total="77.00")
        _order(db, tenant.id, simulator, "confirmado", total="55.00", sandbox=True)

        stranger = await consultar_pedidos(_context(db, tenant, mine), {})
        tried = await consultar_pedidos(_context(db, tenant, simulator, sandbox=True), {})
        real = await consultar_pedidos(_context(db, tenant, simulator), {})

        assert stranger.status == "no_disponible"
        assert "55.00" in (tried.texto or "") and real.status == "no_disponible"

    async def test_a_cancelled_order_with_money_owed_is_for_a_person(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        order = _order(db, tenant.id, conversation, "descartado")
        order.devolucion_pendiente = True
        db.flush()

        text = (await consultar_pedidos(_context(db, tenant, conversation), {})).texto or ""

        assert "cancelado" in text and "devolución pendiente" in text
