"""The `gestionar_pedido` tool (Feature 004, TASK-426).

The verification of the task: a quoted product answers `no_disponible`; a package
of 5 with 2 selections is not complete; "I'll think about it" with no order still
registers the promotion and prices it into the order created within its validity
(and not after); a confirmed free theme is not blocked by the catalogue guard;
`confirmar` only closes a cash-on-delivery order at the charge stage; and prices
never come from the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.orders import gestionar_pedido, offers_of
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import BusinessProfile, Product
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order
from app.models.sales_config import Promotion
from app.models.tenant import CASH_ORDER, Tenant
from app.process.engine import compute_stage
from app.process.facts import Facts, FactsConfig, FactsLoader
from app.process.templates import TEMPLATES
from app.process.templates.base import ProcessOptions, Stage
from app.providers.llm import MockLLMProvider
from app.repositories.notification import TenantNotificationRepository
from app.repositories.orders import OrderRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.services.tenant_alerts import TenantAlerts

D = Decimal
SHOP = TEMPLATES["productos"]
CERT = TEMPLATES["certificaciones"]


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name=f"Capacita {uuid.uuid4().hex[:6]}")
    db.add(row)
    db.flush()
    db.add(BusinessProfile(tenant_id=row.id, business_name="Capacita", currency="PEN"))
    db.flush()
    return row


@pytest.fixture
def catalogue(db: Session, tenant: Tenant) -> dict[str, Product]:
    items = {
        "excel": Product(tenant_id=tenant.id, name="Excel", price=D("100.00"), category="Cursos"),
        "word": Product(tenant_id=tenant.id, name="Word", price=D("80.00"), category="Cursos"),
        "siaf": Product(
            tenant_id=tenant.id, name="SIAF", price=D("120.00"), category="Gestión", es_digital=True
        ),
        "free5": Product(
            tenant_id=tenant.id,
            name="Paquete 5 certificaciones",
            price=D("500.00"),
            bundle_size=5,
            bundle_permite_libres=True,
            es_digital=True,
        ),
        "closed2": Product(
            tenant_id=tenant.id,
            name="Combo de 2",
            price=D("150.00"),
            bundle_size=2,
            bundle_permite_libres=False,
        ),
        "quote": Product(
            tenant_id=tenant.id, name="Consultoría", price=D("0.00"), modo_venta="cotizacion"
        ),
    }
    db.add_all(items.values())
    db.flush()
    return items


@pytest.fixture
def contact(db: Session, tenant: Tenant) -> Contact:
    row = Contact(
        tenant_id=tenant.id,
        phone_e164="+51987000333",
        custom_data={"nombres": "Ana"},
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


def make_context(
    db: Session, tenant: Tenant, conversation: Conversation, **overrides: Any
) -> ToolContext:
    values: dict[str, Any] = {
        "session": db,
        "tenant_id": tenant.id,
        "llm": MockLLMProvider(),
        "conversation_id": conversation.id,
        "options": SHOP.resolve(),
        "sandbox": True,
    }
    values.update(overrides)
    return ToolContext(**values)


async def act(context: ToolContext, **arguments: Any) -> ToolResult:
    return await gestionar_pedido(context, arguments)


def active_order(db: Session, tenant: Tenant, conversation: Conversation) -> Order | None:
    return OrderRepository(db, tenant.id).active_for_conversation(conversation.id)


def promotion(db: Session, tenant: Tenant, **values: Any) -> Promotion:
    base: dict[str, Any] = {
        "tenant_id": tenant.id,
        "nombre": "Cupón 20 %",
        "uso": "cierre",
        "tipo": "porcentaje",
        "valor": D("20"),
        "aplica_a": "todo",
        "vigencia_horas": 24,
    }
    base.update(values)
    row = Promotion(**base)
    db.add(row)
    db.flush()
    return row


class TestAddingAndChangingLines:
    async def test_adding_returns_the_whole_order_priced_by_the_system(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)

        result = await act(
            context, accion="agregar", producto_id=str(catalogue["excel"].id), cantidad=2
        )

        assert result.status == "hecho"
        pedido = result.data["pedido"]
        assert pedido["items"][0] == {
            "producto_id": str(catalogue["excel"].id),
            "nombre": "Excel",
            "cantidad": 2,
            "precio_unitario": "100.00",
            "subtotal": "200.00",
            "selecciones": [],
            "faltan_selecciones": 0,
        }
        assert (pedido["subtotal"], pedido["descuento"], pedido["envio"], pedido["total"]) == (
            "200.00",
            "0.00",
            "0.00",
            "200.00",
        )
        assert pedido["moneda"] == "PEN"
        assert pedido["estado"] == "borrador"
        assert "Total: S/ 200.00" in (result.texto or "")
        order = active_order(db, tenant, conversation)
        assert order is not None and order.is_sandbox is True

    async def test_a_price_sent_by_the_model_is_ignored(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)

        result = await act(
            context,
            accion="agregar",
            producto_id=str(catalogue["excel"].id),
            precio="1.00",
            total="1.00",
            descuento="99.00",
        )

        assert result.data["pedido"]["total"] == "100.00"

    async def test_a_new_order_copies_the_payment_mode_of_the_options(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        options = SHOP.resolve(ProcessOptions(modalidad_pago="contra_entrega"))
        context = make_context(db, tenant, conversation, options=options)

        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        order = active_order(db, tenant, conversation)
        assert order is not None and order.modalidad_pago == "contra_entrega"

    async def test_a_quoted_product_is_not_available_and_creates_no_order(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)

        result = await act(context, accion="agregar", producto_id=str(catalogue["quote"].id))

        assert result.status == "no_disponible"
        assert "derivar_a_humano" in (result.decir or "")
        assert "cotizacion" in (result.decir or "")
        assert result.data["pedido"] is None
        assert active_order(db, tenant, conversation) is None

    @pytest.mark.parametrize("which", ["unknown", "deleted"])
    async def test_a_product_that_does_not_exist_is_not_available(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
        which: str,
    ) -> None:
        product_id = uuid.uuid4()
        if which == "deleted":
            catalogue["word"].is_active = False
            db.flush()
            product_id = catalogue["word"].id

        result = await act(
            make_context(db, tenant, conversation), accion="agregar", producto_id=str(product_id)
        )

        assert result.status == "no_disponible"
        assert active_order(db, tenant, conversation) is None

    async def test_malformed_arguments_are_errors(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        good = str(catalogue["excel"].id)

        assert (await act(context, accion="bailar")).status == "error"
        assert (await act(context, accion="agregar", producto_id="no-es-uuid")).status == "error"
        assert (
            await act(context, accion="agregar", producto_id=good, cantidad="muchas")
        ).status == "error"
        assert (
            await act(context, accion="agregar", producto_id=good, cantidad=0)
        ).status == "error"
        assert (
            await act(context, accion="agregar", producto_id=good, cantidad=True)
        ).status == "error"
        assert (
            await act(context, accion="agregar", producto_id=good, cantidad=2.5)
        ).status == "error"
        assert active_order(db, tenant, conversation) is None

    async def test_a_quantity_given_as_text_is_understood(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        result = await act(
            make_context(db, tenant, conversation),
            accion="agregar",
            producto_id=str(catalogue["excel"].id),
            cantidad="3",
        )

        assert result.data["pedido"]["items"][0]["cantidad"] == 3

    async def test_changing_the_quantity_removing_and_emptying(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        excel, word = str(catalogue["excel"].id), str(catalogue["word"].id)
        await act(context, accion="agregar", producto_id=excel)
        await act(context, accion="agregar", producto_id=word)

        changed = await act(context, accion="cantidad", producto_id=excel, cantidad=3)
        assert changed.data["pedido"]["total"] == "380.00"
        assert (
            await act(context, accion="cantidad", producto_id=excel, cantidad="x")
        ).status == "error"
        assert (
            await act(context, accion="cantidad", producto_id=excel, cantidad=1000)
        ).status == "error"
        assert (
            await act(context, accion="agregar", producto_id=excel, cantidad=999)
        ).status == "error"

        removed = await act(context, accion="quitar", producto_id=word)
        assert [i["nombre"] for i in removed.data["pedido"]["items"]] == ["Excel"]

        emptied = await act(context, accion="vaciar")
        assert emptied.data["pedido"]["items"] == []
        assert emptied.data["pedido"]["total"] == "0.00"

    async def test_a_product_not_in_the_order_cannot_be_changed(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(
            context, accion="cantidad", producto_id=str(catalogue["word"].id), cantidad=2
        )

        assert result.status == "no_disponible"
        assert result.data["pedido"]["total"] == "100.00"

    async def test_without_an_order_there_is_nothing_to_change(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        context = make_context(db, tenant, conversation)
        product = str(uuid.uuid4())

        for action in ("quitar", "cantidad", "elegir", "vaciar"):
            result = await act(context, accion=action, producto_id=product, cantidad=1)
            assert result.status == "no_disponible", action
            assert result.data["pedido"] is None

    async def test_viewing_without_an_order_returns_null(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        result = await act(make_context(db, tenant, conversation), accion="ver")

        assert result.status == "hecho"
        assert result.data == {"pedido": None}

    async def test_viewing_shows_the_current_order(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(context, accion="ver")

        assert result.status == "hecho"
        assert result.data["pedido"]["items"][0]["nombre"] == "Excel"

    async def test_the_template_limit_on_lines_is_enforced(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation, max_lines=1)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(context, accion="agregar", producto_id=str(catalogue["word"].id))

        assert result.status == "no_disponible"
        assert "máximo 1" in (result.decir or "")
        assert len(result.data["pedido"]["items"]) == 1

    async def test_the_registry_hands_the_template_limit_to_the_tool(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        services = TEMPLATES["servicios"]
        registry = registry_for_stage(services, Stage.PEDIDO, Facts(), services.resolve())
        tool = registry.get("gestionar_pedido")
        assert tool is not None and tool.effect is True
        context = make_context(db, tenant, conversation, options=services.resolve())
        for key in ("excel", "word", "siaf"):
            await tool.handler(
                context, {"accion": "agregar", "producto_id": str(catalogue[key].id)}
            )

        result = await tool.handler(
            context, {"accion": "agregar", "producto_id": str(catalogue["free5"].id)}
        )

        assert isinstance(result, ToolResult)
        assert result.status == "no_disponible"
        assert "máximo 3" in (result.decir or "")

    async def test_an_order_in_review_cannot_be_changed(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))
        order = active_order(db, tenant, conversation)
        assert order is not None
        order.estado = "por_confirmar"
        db.flush()

        result = await act(context, accion="agregar", producto_id=str(catalogue["word"].id))

        assert result.status == "no_disponible"
        assert "revisión" in (result.decir or "")
        assert result.data["pedido"]["estado"] == "por_confirmar"

    async def test_a_change_asked_during_review_is_noted_for_the_operator(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        """Milestone B, decision 8: explained to the customer, left on the order."""
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))
        order = active_order(db, tenant, conversation)
        assert order is not None
        order.estado = "por_confirmar"
        db.flush()

        result = await act(
            context, accion="solicitar_cambio", nota="  Quiere  cambiar Excel por Word  "
        )

        assert result.status == "hecho"
        assert "no se puede cambiar ahora" in (result.decir or "")
        db.refresh(order)
        [note] = order.notas
        assert (note["texto"], note["origen"]) == ("Quiere cambiar Excel por Word", "cliente")
        # The order itself did not change.
        assert order.estado == "por_confirmar"
        assert [line["nombre"] for line in result.data["pedido"]["items"]] == ["Excel"]

    async def test_a_change_request_needs_an_order_under_review_and_a_note(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        draft = await act(context, accion="solicitar_cambio", nota="otra cosa")
        order = active_order(db, tenant, conversation)
        assert order is not None
        order.estado = "por_confirmar"
        db.flush()
        empty = await act(context, accion="solicitar_cambio", nota="   ")

        assert draft.status == "no_disponible"
        assert empty.status == "error"
        assert order.notas == []

    async def test_after_a_confirmed_order_adding_starts_a_new_one(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        first = await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))
        order = active_order(db, tenant, conversation)
        assert order is not None
        order.estado = "confirmado"
        db.flush()

        second = await act(context, accion="agregar", producto_id=str(catalogue["word"].id))

        assert second.status == "hecho"
        assert second.data["pedido"]["numero"] == first.data["pedido"]["numero"] + 1
        assert [i["nombre"] for i in second.data["pedido"]["items"]] == ["Word"]

    async def test_any_change_ends_a_rejection(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))
        order = active_order(db, tenant, conversation)
        assert order is not None
        order.rechazo_vigente = True
        db.flush()

        await act(context, accion="agregar", producto_id=str(catalogue["word"].id))

        assert order.rechazo_vigente is False

    async def test_without_a_contact_the_conversation_cannot_order(
        self,
        db: Session,
        tenant: Tenant,
        catalogue: dict[str, Product],
    ) -> None:
        lonely = Conversation(tenant_id=tenant.id, channel="sandbox")
        db.add(lonely)
        db.flush()

        result = await act(
            make_context(db, tenant, lonely),
            accion="agregar",
            producto_id=str(catalogue["excel"].id),
        )

        assert result.status == "no_disponible"

    async def test_no_conversation_means_nothing_to_work_on(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        context = make_context(db, tenant, conversation, conversation_id=None)

        assert (await act(context, accion="ver")).status == "error"
        missing = make_context(db, tenant, conversation, conversation_id=uuid.uuid4())
        assert (await act(missing, accion="ver")).status == "error"


class TestPackages:
    async def test_a_package_of_five_with_two_selections_is_not_complete(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation, options=CERT.resolve())
        await act(context, accion="agregar", producto_id=str(catalogue["free5"].id))

        result = await act(context, accion="elegir", seleccion="SIAF, Gestión pública")

        pedido = result.data["pedido"]
        assert result.status == "hecho"
        assert result.data["elegidos"] == ["SIAF", "Gestión pública"]
        assert pedido["completo"] is False
        assert pedido["faltan_selecciones"] == 3
        assert pedido["items"][0]["selecciones"] == ["SIAF", "Gestión pública"]
        assert "NO está completo" in (result.texto or "")

        # The engine reads it back from the database: PEDIDO, with 3 missing.
        facts = FactsLoader(db, tenant.id, FactsConfig(options=CERT.resolve())).load(conversation)
        assert facts.pedido is not None
        assert (facts.pedido.completo, facts.pedido.faltan_selecciones) == (False, 3)
        assert (
            compute_stage(
                CERT,
                CERT.resolve(),
                facts.model_copy(update={"saludo_enviado": True}),
                datetime.now(UTC),
            )
            is Stage.PEDIDO
        )

    async def test_the_last_selection_completes_it(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["free5"].id))

        result = await act(context, accion="elegir", seleccion="A; B\nC, D , E")

        assert result.data["pedido"]["completo"] is True
        assert result.data["pedido"]["faltan_selecciones"] == 0
        assert "El pedido está completo" in (result.texto or "")

    async def test_a_free_theme_is_remembered_for_the_catalogue_guard(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["free5"].id))

        await act(context, accion="elegir", seleccion="Excel avanzado")

        assert context.extra_names == ["Excel avanzado"]

    async def test_a_closed_package_takes_only_catalogue_products(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["closed2"].id))

        refused = await act(context, accion="elegir", seleccion="Tema inventado")
        taken = await act(context, accion="elegir", seleccion="excel")

        assert refused.status == "no_disponible"
        assert "Excel" in (refused.decir or "")
        assert context.extra_names == ["Excel"]
        assert taken.status == "hecho"
        assert taken.data["pedido"]["items"][0]["selecciones"] == ["Excel"]

    async def test_a_catalogue_product_can_be_chosen_by_its_id(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["closed2"].id))

        result = await act(context, accion="elegir", producto_id=str(catalogue["word"].id))

        assert result.status == "hecho"
        assert result.data["pedido"]["items"][0]["selecciones"] == ["Word"]

    async def test_choosing_with_nothing_to_choose_is_an_error(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["free5"].id))

        assert (await act(context, accion="elegir")).status == "error"
        assert (
            await act(context, accion="elegir", producto_id=str(uuid.uuid4()))
        ).status == "error"

    async def test_repeating_a_choice_is_omitted_and_overflowing_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["closed2"].id))
        await act(context, accion="elegir", seleccion="Excel")

        again = await act(context, accion="elegir", seleccion="excel")
        assert again.status == "omitido"

        await act(context, accion="elegir", seleccion="Word")
        overflow = await act(context, accion="elegir", seleccion="SIAF")
        assert overflow.status == "no_disponible"
        assert "ya tiene todas" in (overflow.decir or "")

    async def test_part_of_a_list_is_kept_when_the_rest_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["closed2"].id))

        result = await act(context, accion="elegir", seleccion="Excel, Tema inventado, Word")

        assert result.status == "hecho"
        assert result.data["elegidos"] == ["Excel"]
        assert "solo admite productos del catálogo" in (result.decir or "")

    async def test_choosing_without_a_package_is_not_available(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(context, accion="elegir", seleccion="SIAF")

        assert result.status == "no_disponible"
        assert "paquete" in (result.decir or "")

    async def test_a_selection_can_be_taken_back(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        package = str(catalogue["free5"].id)
        await act(context, accion="agregar", producto_id=package)
        await act(context, accion="elegir", seleccion="SIAF, Power BI")

        removed = await act(context, accion="quitar", producto_id=package, seleccion="siaf")
        assert removed.data["pedido"]["items"][0]["selecciones"] == ["Power BI"]

        missing = await act(context, accion="quitar", producto_id=package, seleccion="SIAF")
        assert missing.status == "no_disponible"


class TestPromotions:
    async def test_i_will_think_about_it_without_an_order_registers_the_offer(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        coupon = promotion(db, tenant)
        context = make_context(db, tenant, conversation)

        result = await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        assert result.status == "hecho"
        assert result.data["pedido"] is None
        assert "cuando arme su pedido" in (result.decir or "")
        expiry = datetime.fromisoformat(context.facts_patch["promos_ofrecidas"][str(coupon.id)])
        assert timedelta(hours=23, minutes=59) < expiry - datetime.now(UTC) <= timedelta(hours=24)
        assert active_order(db, tenant, conversation) is None

    async def test_the_offer_is_priced_into_the_order_created_within_its_validity(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        coupon = promotion(db, tenant)
        offered = {str(coupon.id): datetime.now(UTC) + timedelta(hours=3)}
        context = make_context(db, tenant, conversation, facts=Facts(promos_ofrecidas=offered))

        result = await act(
            context, accion="agregar", producto_id=str(catalogue["excel"].id), cantidad=2
        )

        pedido = result.data["pedido"]
        assert (pedido["subtotal"], pedido["descuento"], pedido["total"]) == (
            "200.00",
            "40.00",
            "160.00",
        )
        assert pedido["promocion"] == {"nombre": "Cupón 20 %", "ahorro": "40.00"}
        assert "Cupón 20 %" in (result.texto or "")

    async def test_the_offer_is_not_priced_after_its_validity(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        coupon = promotion(db, tenant)
        expired = {str(coupon.id): datetime.now(UTC) - timedelta(minutes=1)}
        context = make_context(db, tenant, conversation, facts=Facts(promos_ofrecidas=expired))

        result = await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        assert result.data["pedido"]["descuento"] == "0.00"
        assert result.data["pedido"]["promocion"] is None

    async def test_a_closing_promotion_that_was_never_offered_does_not_apply(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        promotion(db, tenant)
        context = make_context(db, tenant, conversation)

        result = await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        assert result.data["pedido"]["descuento"] == "0.00"

    async def test_applying_a_promotion_to_an_existing_order(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        coupon = promotion(db, tenant)
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id), cantidad=2)
        order = active_order(db, tenant, conversation)
        assert order is not None
        order.rechazo_vigente = True
        db.flush()

        result = await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        assert result.status == "hecho"
        assert result.data["pedido"]["total"] == "160.00"
        assert order.rechazo_vigente is False
        assert str(coupon.id) in offers_of(context)

    async def test_a_later_change_keeps_the_offer_made_in_this_turn(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        coupon = promotion(db, tenant)
        context = make_context(db, tenant, conversation)
        await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        result = await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        assert result.data["pedido"]["descuento"] == "20.00"

    async def test_a_promotion_the_order_cannot_use_is_refused_and_not_registered(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        coupon = promotion(db, tenant, monto_minimo=D("500"))
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        assert result.status == "no_disponible"
        assert "no aplica" in (result.decir or "")
        assert "promos_ofrecidas" not in context.facts_patch

    async def test_an_automatic_or_unknown_promotion_cannot_be_offered(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        automatic = promotion(db, tenant, uso="automatica", vigencia_horas=None)
        context = make_context(db, tenant, conversation)

        for promotion_id in (automatic.id, uuid.uuid4()):
            result = await act(context, accion="aplicar_promocion", promocion_id=str(promotion_id))
            assert result.status == "no_disponible"
        assert (await act(context, accion="aplicar_promocion", promocion_id="x")).status == "error"
        assert "promos_ofrecidas" not in context.facts_patch

    async def test_an_expired_promotion_cannot_be_offered(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        old = promotion(db, tenant, termina_en=datetime.now(UTC) - timedelta(days=1))

        result = await act(
            make_context(db, tenant, conversation),
            accion="aplicar_promocion",
            promocion_id=str(old.id),
        )

        assert result.status == "no_disponible"

    async def test_offering_again_keeps_the_original_expiry(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        coupon = promotion(db, tenant)
        first_expiry = datetime.now(UTC) + timedelta(hours=2)
        context = make_context(
            db,
            tenant,
            conversation,
            facts=Facts(promos_ofrecidas={str(coupon.id): first_expiry}),
        )

        await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        kept = datetime.fromisoformat(context.facts_patch["promos_ofrecidas"][str(coupon.id)])
        assert kept == first_expiry

    async def test_expired_offers_are_dropped_from_the_stored_facts(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        coupon = promotion(db, tenant)
        stale = uuid.uuid4()
        context = make_context(
            db,
            tenant,
            conversation,
            facts=Facts(promos_ofrecidas={str(stale): datetime.now(UTC) - timedelta(days=2)}),
        )

        await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        assert set(context.facts_patch["promos_ofrecidas"]) == {str(coupon.id)}

    async def test_a_better_automatic_promotion_wins_and_the_model_is_told(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        coupon = promotion(db, tenant, valor=D("10"))
        promotion(
            db, tenant, nombre="Gran oferta", uso="automatica", vigencia_horas=None, valor=D("30")
        )
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(context, accion="aplicar_promocion", promocion_id=str(coupon.id))

        assert result.status == "hecho"
        assert result.data["pedido"]["promocion"]["nombre"] == "Gran oferta"
        assert "no se acumulan" in (result.decir or "")


class TestConfirming:
    def cash_context(
        self, db: Session, tenant: Tenant, conversation: Conversation, **overrides: Any
    ) -> ToolContext:
        options = SHOP.resolve(ProcessOptions(modalidad_pago="contra_entrega"))
        return make_context(db, tenant, conversation, options=options, **overrides)

    async def test_it_sends_a_complete_cash_on_delivery_order_to_review(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = self.cash_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["siaf"].id))
        contact.custom_data = {**contact.custom_data, "nombres": "Ana"}

        result = await act(context, accion="confirmar")

        assert result.status == "hecho"
        assert result.data["pedido"]["estado"] == "por_confirmar"
        assert "no digas que quedó confirmado" in (result.decir or "")
        order = active_order(db, tenant, conversation)
        assert order is not None
        assert order.datos_cliente == {"nombres": "Ana", "telefono": "+51987000333"}

    async def test_the_business_is_told_about_a_cash_order_but_not_about_a_simulated_one(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        """TASK-431: `pedido_contra_entrega` for a real order; nothing in the simulator."""
        alerts = TenantAlerts(db, tenant.id)
        real = self.cash_context(db, tenant, conversation, sandbox=False, notices=alerts)
        await act(real, accion="agregar", producto_id=str(catalogue["siaf"].id))

        await act(real, accion="confirmar")

        [notice] = TenantNotificationRepository(db, tenant.id).recent(CASH_ORDER)
        order = active_order(db, tenant, conversation)
        assert order is not None
        assert notice.payload["order_id"] == str(order.id)
        assert notice.payload["numero"] == order.numero

        simulated = Conversation(tenant_id=tenant.id, channel="sandbox", contact_id=contact.id)
        db.add(simulated)
        db.flush()
        quiet = TenantAlerts(db, tenant.id)
        fake = self.cash_context(db, tenant, simulated, sandbox=True, notices=quiet)
        await act(fake, accion="agregar", producto_id=str(catalogue["siaf"].id))
        await act(fake, accion="confirmar")
        assert len(TenantNotificationRepository(db, tenant.id).recent(CASH_ORDER)) == 1

    async def test_a_prepaid_order_cannot_be_confirmed_by_the_assistant(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = make_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["siaf"].id))

        result = await act(context, accion="confirmar")

        assert result.status == "no_disponible"
        assert result.data["pedido"]["estado"] == "borrador"

    async def test_confirming_without_an_order_is_not_available(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        result = await act(self.cash_context(db, tenant, conversation), accion="confirmar")

        assert result.status == "no_disponible"

    async def test_an_incomplete_order_is_not_confirmed(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        context = self.cash_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["free5"].id))

        result = await act(context, accion="confirmar")

        assert result.status == "no_disponible"
        assert "no está completo" in (result.decir or "")

    async def test_missing_customer_data_blocks_it(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        options = SHOP.resolve(
            ProcessOptions(modalidad_pago="contra_entrega", datos_requeridos=["nombres", "dni"])
        )
        context = make_context(db, tenant, conversation, options=options)
        await act(context, accion="agregar", producto_id=str(catalogue["siaf"].id))

        result = await act(context, accion="confirmar")

        assert result.status == "no_disponible"
        assert "dni" in (result.decir or "")
        assert result.data["pedido"]["estado"] == "borrador"

    async def test_delivery_data_is_required_for_a_physical_product(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        BusinessInfoRepository(db, tenant.id).upsert(
            "entregas",
            {"hace_delivery": True, "recojo_en_tienda": True, "zonas": [], "notas": None},
        )
        context = self.cash_context(db, tenant, conversation)
        await act(context, accion="agregar", producto_id=str(catalogue["excel"].id))

        result = await act(context, accion="confirmar")

        assert result.status == "no_disponible"
        assert "tipo_entrega" in (result.decir or "")

    async def test_the_stage_guard_refuses_it_outside_the_charge_stage(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        cash = Facts(modalidad_pago="contra_entrega")
        options = SHOP.resolve(ProcessOptions(modalidad_pago="contra_entrega"))
        context = make_context(db, tenant, conversation, options=options)
        await act(context, accion="agregar", producto_id=str(catalogue["siaf"].id))

        for stage in (Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS):
            tool = registry_for_stage(SHOP, stage, cash, options).get("gestionar_pedido")
            assert tool is not None
            refused = await tool.handler(context, {"accion": "confirmar"})
            assert isinstance(refused, ToolResult) and refused.status == "no_disponible"
        order = active_order(db, tenant, conversation)
        assert order is not None and order.estado == "borrador"

        tool = registry_for_stage(SHOP, Stage.COBRO, cash, options).get("gestionar_pedido")
        assert tool is not None
        done = await tool.handler(context, {"accion": "confirmar"})
        assert isinstance(done, ToolResult) and done.status == "hecho"
        assert order.estado == "por_confirmar"
