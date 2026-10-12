"""`gestionar_pedido` inside the agent loop (Feature 004, TASK-426).

Two things only the loop can get wrong, and both would be silent:

* **The catalogue guard** compares the reply with the catalogue's names. A free
  theme the customer chose ("Excel avanzado" when the catalogue only has "Excel")
  looks like an invented product to it. A confirmed theme must not be blocked.
* **The per-turn cache** answers a repeated tool call without running it. For a
  tool that writes that is only safe while nothing else has written: `ver` after
  `agregar` must show the new order, and an identical `agregar` repeated by the
  model must not add the product twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.core import CATALOG_GUARD_REPLY, AgentCore, AgentReply
from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.registry import ToolContext
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.process.facts import Facts, Pedido, PedidoItem
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall
from app.repositories.orders import OrderItemRepository, OrderRepository

D = Decimal
CERT = TEMPLATES["certificaciones"]


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name=f"Capacita {uuid.uuid4().hex[:6]}")
    db.add(row)
    db.flush()
    db.add_all(
        [
            BusinessProfile(tenant_id=row.id, business_name="Capacita", industry="instituto"),
            AgentConfig(tenant_id=row.id, agent_name="Leo"),
        ]
    )
    db.flush()
    return row


@pytest.fixture
def catalogue(db: Session, tenant: Tenant) -> dict[str, Product]:
    items = {
        "excel": Product(tenant_id=tenant.id, name="Excel", price=D("100.00")),
        "word": Product(tenant_id=tenant.id, name="Word", price=D("80.00")),
        "package": Product(
            tenant_id=tenant.id,
            name="Paquete 5 certificaciones",
            price=D("500.00"),
            bundle_size=5,
            bundle_permite_libres=True,
            es_digital=True,
        ),
    }
    db.add_all(items.values())
    db.flush()
    return items


@pytest.fixture
def conversation(db: Session, tenant: Tenant) -> Conversation:
    contact = Contact(
        tenant_id=tenant.id, phone_e164="+51987000444", last_inbound_at=datetime.now(UTC)
    )
    db.add(contact)
    db.flush()
    row = Conversation(tenant_id=tenant.id, channel="sandbox", contact_id=contact.id)
    db.add(row)
    db.flush()
    return row


async def _turn(
    db: Session,
    tenant: Tenant,
    conversation: Conversation,
    llm: MockLLMProvider,
    *,
    facts: Facts | None = None,
) -> tuple[AgentReply, ToolContext]:
    facts = facts or Facts(saludo_enviado=True)
    options = CERT.resolve()
    registry = registry_for_stage(CERT, Stage.PEDIDO, facts, options)
    context = ToolContext(
        session=db,
        tenant_id=tenant.id,
        llm=llm,
        conversation_id=conversation.id,
        options=options,
        sandbox=True,
        facts=facts,
    )
    reply = await AgentCore(db, tenant.id, llm=llm, registry=registry).respond(
        conversation_id=conversation.id, customer_message="quiero el paquete", context=context
    )
    return reply, context


def _tool_outputs(llm: MockLLMProvider) -> list[str]:
    return [m.content for m in llm.chat_calls[1].messages if m.role == "tool"]


class TestAConfirmedFreeThemeIsNotBlockedByTheCatalogueGuard:
    async def test_the_theme_chosen_this_turn_can_be_repeated_in_the_reply(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _call(
                            "gestionar_pedido",
                            accion="agregar",
                            producto_id=str(catalogue["package"].id),
                        ),
                        _call("gestionar_pedido", accion="elegir", seleccion="Excel avanzado"),
                    ),
                ),
                ChatResult(content="Listo, anoté Excel avanzado en tu paquete. ¿Qué otro tema?"),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        assert reply.catalog_guard is None
        assert reply.degraded is False
        assert "Excel avanzado" in reply.content
        assert len(llm.chat_calls) == 2  # no correction round

    async def test_a_theme_already_in_the_order_is_remembered_by_the_next_turn(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        facts = Facts(
            saludo_enviado=True,
            pedido=Pedido(
                items=[
                    PedidoItem(nombre="Paquete 5 certificaciones", selecciones=["Excel avanzado"])
                ]
            ),
        )
        llm = MockLLMProvider(
            responses=[ChatResult(content="Ya tienes Excel avanzado. ¿Cuál es el siguiente?")]
        )

        reply, _ = await _turn(db, tenant, conversation, llm, facts=facts)

        assert reply.catalog_guard is None
        assert "Excel avanzado" in reply.content

    async def test_without_the_choice_the_same_reply_is_stopped(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        """The control: the guard does fire on that wording when nothing was chosen."""
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="Te recomiendo Excel avanzado para tu paquete."),
                ChatResult(content="Te recomiendo Excel avanzado, el mejor."),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        assert reply.catalog_guard is not None
        assert reply.catalog_guard["status"] == "blocked"
        assert reply.content == CATALOG_GUARD_REPLY


class TestTheCacheNeverHidesAWrite:
    async def test_viewing_after_adding_shows_the_new_order(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _call("gestionar_pedido", accion="ver"),
                        _call(
                            "gestionar_pedido",
                            accion="agregar",
                            producto_id=str(catalogue["excel"].id),
                        ),
                        _call("gestionar_pedido", accion="ver"),
                    ),
                ),
                ChatResult(content="Listo."),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        before, _added, after = _tool_outputs(llm)
        assert "Todavía no hay un pedido" in before
        assert "Excel" in after
        assert [call["cached"] for call in reply.tools_called] == [False, False, False]

    async def test_an_identical_add_repeated_by_the_model_is_not_added_twice(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        add = {"accion": "agregar", "producto_id": str(catalogue["excel"].id)}
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(_call("gestionar_pedido", **add), _call("gestionar_pedido", **add)),
                ),
                ChatResult(content="Listo."),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        order = OrderRepository(db, tenant.id).active_for_conversation(conversation.id)
        assert order is not None
        lines = OrderItemRepository(db, tenant.id).for_order(order.id)
        assert [(line.nombre, line.cantidad) for line in lines] == [("Excel", 1)]
        assert [call["cached"] for call in reply.tools_called] == [False, True]

    async def test_the_same_add_after_another_write_runs_again(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        add_excel = {"accion": "agregar", "producto_id": str(catalogue["excel"].id)}
        add_word = {"accion": "agregar", "producto_id": str(catalogue["word"].id)}
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _call("gestionar_pedido", **add_excel),
                        _call("gestionar_pedido", **add_word),
                        _call("gestionar_pedido", **add_excel),
                    ),
                ),
                ChatResult(content="Listo."),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        order = OrderRepository(db, tenant.id).active_for_conversation(conversation.id)
        assert order is not None
        lines = OrderItemRepository(db, tenant.id).for_order(order.id)
        assert {line.nombre: line.cantidad for line in lines} == {"Excel": 2, "Word": 1}
        assert [call["cached"] for call in reply.tools_called] == [False, False, False]

    async def test_read_only_tools_are_still_cached(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        search: dict[str, Any] = {"consulta": "excel"}
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _call("buscar_productos", **search),
                        _call(
                            "gestionar_pedido",
                            accion="agregar",
                            producto_id=str(catalogue["excel"].id),
                        ),
                        _call("buscar_productos", **search),
                    ),
                ),
                ChatResult(content="Listo."),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        assert [call["cached"] for call in reply.tools_called] == [False, False, True]

    async def test_a_failing_write_invalidates_what_was_cached_before(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.agent.tools import orders as orders_module

        real = orders_module._ACTIONS["vaciar"]

        def boom(turn: Any, arguments: Any) -> Any:
            real(turn, arguments)
            raise RuntimeError("the database went away")

        monkeypatch.setitem(orders_module._ACTIONS, "vaciar", boom)
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _call(
                            "gestionar_pedido",
                            accion="agregar",
                            producto_id=str(catalogue["excel"].id),
                        ),
                        _call("gestionar_pedido", accion="ver"),
                        _call("gestionar_pedido", accion="vaciar"),
                        _call("gestionar_pedido", accion="ver"),
                    ),
                ),
                ChatResult(content="Listo."),
            ]
        )

        reply, _ = await _turn(db, tenant, conversation, llm)

        outputs = _tool_outputs(llm)
        assert outputs[2].startswith("ERROR:")
        assert "No tiene productos" in outputs[3]
        assert [call["cached"] for call in reply.tools_called] == [False, False, False, False]
