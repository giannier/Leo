"""The `registrar_datos_cliente` tool (Feature 004, TASK-427).

The verification of the task: a 7-digit DNI is invalid; a zone that does not exist
answers with the valid ones; a business that only offers pickup gets `tipo_entrega`
set by itself; a digital product asks for no address; and the data never appear in
the traces, which are masked.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline
from app.agent.tools.customer import registrar_datos_cliente
from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.orders import gestionar_pedido
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.channel import Contact
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.models.orders import Order
from app.models.tenant import Tenant
from app.process.facts import Facts, FactsConfig, FactsLoader
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.templates import TEMPLATES
from app.process.templates.base import ProcessOptions, Stage
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall
from app.repositories.orders import OrderRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.services.turn_outbound import SandboxOutbound

D = Decimal
SHOP = TEMPLATES["productos"]
ZONES = [
    {"nombre": "Bagua Grande", "costo": "8.00", "tiempo": "24 h"},
    {"nombre": "Jaén", "costo": "15.50", "tiempo": "48 h"},
]
DNI = "12345678"
EMAIL = "ana.quispe@mail.com"
ADDRESS = "Jr. Amazonas 123, Bagua Grande"


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name=f"Tienda {uuid.uuid4().hex[:6]}")
    db.add(row)
    db.flush()
    db.add_all(
        [
            BusinessProfile(tenant_id=row.id, business_name="Tienda", industry="retail"),
            AgentConfig(tenant_id=row.id, agent_name="Leo"),
        ]
    )
    db.flush()
    return row


@pytest.fixture
def catalogue(db: Session, tenant: Tenant) -> dict[str, Product]:
    items = {
        "physical": Product(tenant_id=tenant.id, name="Polo", price=D("50.00")),
        "digital": Product(tenant_id=tenant.id, name="Curso", price=D("120.00"), es_digital=True),
    }
    db.add_all(items.values())
    db.flush()
    return items


@pytest.fixture
def contact(db: Session, tenant: Tenant) -> Contact:
    row = Contact(
        tenant_id=tenant.id,
        phone_e164="+51987000555",
        profile_name="Ana WhatsApp",
        last_inbound_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def conversation(db: Session, tenant: Tenant, contact: Contact) -> Conversation:
    row = Conversation(tenant_id=tenant.id, channel="sandbox", contact_id=contact.id)
    db.add(row)
    db.flush()
    return row


def set_deliveries(db: Session, tenant: Tenant, **overrides: Any) -> None:
    data: dict[str, Any] = {
        "hace_delivery": True,
        "recojo_en_tienda": True,
        "zonas": ZONES,
        "envio_gratis_desde": None,
        "notas": None,
    }
    data.update(overrides)
    BusinessInfoRepository(db, tenant.id).upsert("entregas", data)


def context(
    db: Session, tenant: Tenant, conversation: Conversation, *, required: list[str] | None = None
) -> ToolContext:
    options = SHOP.resolve(
        ProcessOptions(datos_requeridos=required or ["nombres", "dni", "correo"])
    )
    return ToolContext(
        session=db,
        tenant_id=tenant.id,
        llm=MockLLMProvider(),
        conversation_id=conversation.id,
        options=options,
        sandbox=True,
    )


async def save(ctx: ToolContext, **fields: Any) -> ToolResult:
    return await registrar_datos_cliente(ctx, fields)


async def order_with(ctx: ToolContext, product: Product) -> Order:
    await gestionar_pedido(ctx, {"accion": "agregar", "producto_id": str(product.id)})
    assert ctx.conversation_id is not None
    order = OrderRepository(ctx.session, ctx.tenant_id).active_for_conversation(ctx.conversation_id)
    assert order is not None
    return order


class TestValidation:
    async def test_a_seven_digit_dni_is_invalid_and_nothing_else_is_lost(
        self, db: Session, tenant: Tenant, contact: Contact, conversation: Conversation
    ) -> None:
        ctx = context(db, tenant, conversation)

        result = await save(ctx, dni="1234567", nombres="Ana Quispe", correo=EMAIL)

        assert result.status == "hecho"
        assert result.data["guardados"] == ["nombres", "correo"]
        assert list(result.data["invalidos"]) == ["dni"]
        assert "8 dígitos" in result.data["invalidos"]["dni"]
        assert result.data["faltan"] == ["dni"]
        assert contact.custom_data == {"nombres": "Ana Quispe", "correo": EMAIL}

    async def test_the_data_arrive_in_pieces(
        self, db: Session, tenant: Tenant, contact: Contact, conversation: Conversation
    ) -> None:
        ctx = context(db, tenant, conversation)

        first = await save(ctx, nombres="Ana")
        second = await save(ctx, dni=DNI)
        third = await save(ctx, correo=EMAIL)

        assert first.data["faltan"] == ["dni", "correo"]
        assert second.data["faltan"] == ["correo"]
        assert third.data["faltan"] == []
        assert "Ya no falta" in (third.texto or "")
        assert contact.custom_data == {"nombres": "Ana", "dni": DNI, "correo": EMAIL}
        facts = FactsLoader(db, tenant.id, FactsConfig(options=ctx.options)).load(conversation)  # type: ignore[arg-type]
        assert facts.datos_completos is True

    async def test_all_invalid_saves_nothing_and_says_why(
        self, db: Session, tenant: Tenant, contact: Contact, conversation: Conversation
    ) -> None:
        result = await save(context(db, tenant, conversation), dni="12", correo="nada")

        assert result.status == "error"
        assert "No se guardó nada" in (result.decir or "")
        assert contact.custom_data == {}

    async def test_empty_values_are_not_data(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        ctx = context(db, tenant, conversation)

        assert (await save(ctx)).status == "error"
        assert (await save(ctx, dni="", correo="   ", nombres=None)).status == "error"

    async def test_a_field_the_template_does_not_ask_for_is_refused(
        self, db: Session, tenant: Tenant, contact: Contact, conversation: Conversation
    ) -> None:
        result = await save(context(db, tenant, conversation), ruc="20123456789", nombres="Ana")

        assert result.data["guardados"] == ["nombres"]
        assert "ruc" in result.data["invalidos"]
        assert "ruc" not in contact.custom_data

    async def test_the_name_can_be_given_at_any_time_even_if_the_template_does_not_ask(
        self, db: Session, tenant: Tenant, contact: Contact, conversation: Conversation
    ) -> None:
        ctx = context(db, tenant, conversation, required=["dni"])

        result = await save(ctx, nombres="Ana")

        assert result.data["guardados"] == ["nombres"]
        assert contact.custom_data == {"nombres": "Ana"}

    async def test_values_are_stored_normalised(
        self, db: Session, tenant: Tenant, contact: Contact, conversation: Conversation
    ) -> None:
        ctx = context(db, tenant, conversation, required=["dni", "correo", "telefono"])

        await save(ctx, dni="1234 5678", correo=" Ana.Quispe@MAIL.com ", telefono="987 654 321")

        assert contact.custom_data == {
            "dni": DNI,
            "correo": EMAIL,
            "telefono": "+51987654321",
        }

    async def test_a_contact_that_already_has_the_phone_does_not_need_it(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        ctx = context(db, tenant, conversation, required=["nombres", "telefono"])

        result = await save(ctx, nombres="Ana")

        assert result.data["faltan"] == []

    async def test_without_a_contact_nothing_is_saved(self, db: Session, tenant: Tenant) -> None:
        lonely = Conversation(tenant_id=tenant.id, channel="sandbox")
        db.add(lonely)
        db.flush()

        assert (await save(context(db, tenant, lonely), nombres="Ana")).status == "error"
        no_conversation = replace(context(db, tenant, lonely), conversation_id=None)
        assert (await save(no_conversation, nombres="Ana")).status == "error"


class TestDelivery:
    async def test_a_zone_that_does_not_exist_lists_the_valid_ones(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation)
        await order_with(ctx, catalogue["physical"])

        result = await save(ctx, tipo_entrega="delivery", zona_envio="Marte")

        assert result.status == "hecho"  # the type was accepted
        assert result.data["guardados"] == ["tipo_entrega"]
        assert "zona_envio" in result.data["invalidos"]
        assert result.data["zonas_validas"] == ["Bagua Grande", "Jaén"]
        assert "Bagua Grande, Jaén" in (result.texto or "")

    async def test_a_zone_sets_the_shipping_and_the_total(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation, required=["telefono"])
        order = await order_with(ctx, catalogue["physical"])

        result = await save(
            ctx,
            tipo_entrega="delivery",
            zona_envio="jaén",
            direccion=ADDRESS,
            referencia="Frente al parque",
        )

        assert result.data["invalidos"] == {}
        assert (order.tipo_entrega, order.zona_envio) == ("delivery", "Jaén")
        assert (order.envio, order.total) == (D("15.50"), D("65.50"))
        assert contact.custom_data["direccion"] == ADDRESS
        assert result.data["faltan"] == []

    async def test_delivery_asks_for_the_address_and_the_zone(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation, required=["nombres"])
        await order_with(ctx, catalogue["physical"])

        before = await save(ctx, nombres="Ana")
        after = await save(ctx, tipo_entrega="delivery")

        assert before.data["faltan"] == ["tipo_entrega"]
        assert after.data["faltan"] == ["direccion", "zona_envio"]

    async def test_pickup_clears_the_zone(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation, required=["nombres"])
        order = await order_with(ctx, catalogue["physical"])
        await save(ctx, tipo_entrega="delivery", zona_envio="Jaén", direccion=ADDRESS)

        result = await save(ctx, tipo_entrega="recojo")

        assert (order.tipo_entrega, order.zona_envio, order.envio) == ("recojo", None, D("0.00"))
        assert result.data["faltan"] == ["nombres"]

    async def test_an_invalid_delivery_type_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation)
        await order_with(ctx, catalogue["physical"])

        result = await save(ctx, tipo_entrega="dron", nombres="Ana")

        assert "tipo_entrega" in result.data["invalidos"]

    async def test_a_business_that_only_picks_up_sets_the_type_by_itself(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant, hace_delivery=False, zonas=[])
        ctx = context(db, tenant, conversation, required=["nombres"])
        order = await order_with(ctx, catalogue["physical"])
        assert order.tipo_entrega is None

        result = await save(ctx, nombres="Ana")

        assert order.tipo_entrega == "recojo"
        assert result.data["tipo_entrega"] == "recojo"
        assert "solo ofrece recojo" in (result.texto or "")
        assert result.data["faltan"] == []

    async def test_a_business_that_only_delivers_sets_the_type_by_itself(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        """Milestone B, decision 11: the same as pickup, the other way round."""
        set_deliveries(db, tenant, recojo_en_tienda=False, zonas=[])
        ctx = context(db, tenant, conversation, required=["nombres"])
        order = await order_with(ctx, catalogue["physical"])
        assert order.tipo_entrega is None

        result = await save(ctx, nombres="Ana")

        assert order.tipo_entrega == "delivery"
        assert result.data["tipo_entrega"] == "delivery"
        assert "solo hace delivery" in (result.texto or "")
        # The type is never asked; the address still is.
        assert result.data["faltan"] == ["direccion"]

    async def test_a_business_that_only_picks_up_refuses_delivery(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant, hace_delivery=False, zonas=[])
        ctx = context(db, tenant, conversation)
        order = await order_with(ctx, catalogue["physical"])

        result = await save(ctx, tipo_entrega="delivery")

        assert "tipo_entrega" in result.data["invalidos"]
        assert order.tipo_entrega == "recojo"

    async def test_a_digital_product_asks_for_no_address(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation, required=["nombres"])
        await order_with(ctx, catalogue["digital"])

        result = await save(ctx, nombres="Ana", direccion=ADDRESS, tipo_entrega="delivery")

        assert result.data["guardados"] == ["nombres"]
        assert set(result.data["invalidos"]) == {"direccion", "tipo_entrega"}
        assert result.data["faltan"] == []
        assert "direccion" not in contact.custom_data

    async def test_without_an_order_the_delivery_fields_are_not_asked(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        set_deliveries(db, tenant)

        result = await save(context(db, tenant, conversation), tipo_entrega="recojo", nombres="Ana")

        assert result.data["guardados"] == ["nombres"]
        assert "tipo_entrega" in result.data["invalidos"]

    async def test_the_schema_offers_the_delivery_fields_only_when_the_order_needs_them(
        self, db: Session
    ) -> None:
        with_delivery = registry_for_stage(
            SHOP, Stage.DATOS, Facts(requiere_entrega=True), SHOP.resolve()
        ).get("registrar_datos_cliente")
        without = registry_for_stage(SHOP, Stage.DATOS, Facts(), SHOP.resolve()).get(
            "registrar_datos_cliente"
        )

        assert with_delivery is not None and without is not None
        assert "zona_envio" in with_delivery.spec.parameters["properties"]
        assert "zona_envio" not in without.spec.parameters["properties"]
        assert with_delivery.effect is True


class TestARejectionEndsWithAChange:
    async def test_new_data_turns_the_rejection_off(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        ctx = context(db, tenant, conversation)
        order = await order_with(ctx, catalogue["digital"])
        order.rechazo_vigente = True
        db.flush()

        await save(ctx, dni=DNI)

        assert order.rechazo_vigente is False

    async def test_sending_the_same_data_again_is_not_a_change(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        ctx = context(db, tenant, conversation)
        order = await order_with(ctx, catalogue["digital"])
        await save(ctx, dni=DNI)
        order.rechazo_vigente = True
        db.flush()

        await save(ctx, dni=DNI)

        assert order.rechazo_vigente is True

    async def test_the_same_delivery_choice_is_not_a_change_either(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        ctx = context(db, tenant, conversation)
        order = await order_with(ctx, catalogue["physical"])
        await save(ctx, tipo_entrega="delivery", zona_envio="Jaén")
        order.rechazo_vigente = True
        db.flush()

        await save(ctx, tipo_entrega="delivery", zona_envio="JAÉN")

        assert order.rechazo_vigente is True

    async def test_data_given_while_the_order_is_in_review_leave_it_alone(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        ctx = context(db, tenant, conversation)
        order = await order_with(ctx, catalogue["digital"])
        order.estado = "por_confirmar"
        db.flush()

        result = await save(ctx, nombres="Ana")

        assert result.status == "hecho"
        assert order.estado == "por_confirmar"
        assert contact.custom_data == {"nombres": "Ana"}


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


class TestThePersonalDataNeverReachTheTraces:
    async def test_a_turn_that_saves_the_data_leaves_none_of_it_in_the_traces(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
    ) -> None:
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content="mis datos",
                provider_message_id=f"wamid.{uuid.uuid4().hex}",
            )
        )
        db.flush()
        llm = MockLLMProvider(
            responses=[
                ChatResult(
                    content="",
                    tool_calls=(
                        _call(
                            "registrar_datos_cliente",
                            nombres="Ana Quispe",
                            dni=DNI,
                            correo=EMAIL,
                            telefono="987654321",
                        ),
                    ),
                ),
                ChatResult(content="Gracias, Ana. Ya tengo tus datos."),
            ]
        )
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.process_options = {"datos_requeridos": ["nombres", "dni", "correo", "telefono"]}
        db.flush()
        pipeline = TurnPipeline(
            db,
            tenant.id,
            llm=llm,
            outbound=SandboxOutbound(db, tenant.id, conversation.id),
            sandbox=True,
            process_config=ProcessConfigService(db, cache=ProcessConfigCache()),
        )

        result = await pipeline.run(conversation.id, InboundTurn(text="mis datos"))

        assert result.reply is not None
        assert result.reply.tools_called[0]["status"] == "hecho"
        traces = (
            db.execute(select(AgentTrace).where(AgentTrace.tenant_id == tenant.id)).scalars().all()
        )
        events = (
            db.execute(
                select(ConversationStageEvent).where(
                    ConversationStageEvent.conversation_id == conversation.id
                )
            )
            .scalars()
            .all()
        )
        stored = json.dumps(
            [[t.tools_called, t.rag_sources, t.catalog_guard] for t in traces]
            + [e.facts_snapshot for e in events],
            default=str,
            ensure_ascii=False,
        )
        assert traces, "the turn should have been traced"
        for secret in (DNI, EMAIL, "987654321", "Ana Quispe"):
            assert secret not in stored
        assert "****5678" in stored
        # ...while the data are where they belong.
        assert contact.custom_data["dni"] == DNI
        assert contact.custom_data["correo"] == EMAIL

    async def test_what_the_model_reads_back_names_fields_never_values(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        result = await save(
            context(db, tenant, conversation), dni="1234567", correo=EMAIL, nombres="Ana Quispe"
        )

        rendered = result.render()
        for secret in ("1234567", EMAIL, "Ana Quispe"):
            assert secret not in rendered
