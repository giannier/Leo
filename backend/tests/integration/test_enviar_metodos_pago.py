"""The `enviar_metodos_pago` tool (Feature 004, TASK-428).

The verification of the task: the second call for the same order is `omitido`; a
resend is allowed after a rejection; a new order after a closed one gets the
methods again; and the funnel moves to CUENTAS_ENVIADAS.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budget import TurnBudget
from app.agent.outbound import OutboundFailed
from app.agent.pipeline import InboundTurn, TurnPipeline
from app.agent.tools.orders import gestionar_pedido
from app.agent.tools.payments import enviar_metodos_pago, methods_text
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import AgentConfig, BusinessProfile, PaymentMethodBusiness, Product
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import FunnelCode
from app.models.media import MediaAsset
from app.models.orders import Order
from app.models.tenant import Tenant
from app.process.engine import funnel_for
from app.process.facts import FactsConfig, FactsLoader
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall
from app.providers.whatsapp.base import Button, ListSection
from app.repositories.media import MediaAssetRepository
from app.repositories.orders import OrderRepository, PaymentProofRepository
from app.services.turn_outbound import SandboxOutbound

D = Decimal
SHOP = TEMPLATES["productos"]


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
def product(db: Session, tenant: Tenant) -> Product:
    row = Product(tenant_id=tenant.id, name="Curso", price=D("120.00"), es_digital=True)
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def contact(db: Session, tenant: Tenant) -> Contact:
    row = Contact(
        tenant_id=tenant.id,
        phone_e164="+51987000666",
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


def method(db: Session, tenant: Tenant, code: str, instructions: str, **values: Any) -> None:
    db.add(
        PaymentMethodBusiness(tenant_id=tenant.id, method=code, instructions=instructions, **values)
    )
    db.flush()


def asset(db: Session, tenant: Tenant, clave: str, **values: Any) -> MediaAsset:
    data: dict[str, Any] = {
        "clave": clave,
        "tipo": "imagen",
        "titulo": f"QR {clave}",
        "cuando_usar": "Cuando el cliente va a pagar",
        "storage_key": f"media/{uuid.uuid4()}.png",
        "filename": f"{clave}.png",
        "mime_type": "image/png",
        "size_bytes": 10,
    }
    data.update(values)
    return MediaAssetRepository(db, tenant.id).add(**data)


class RecordingOutbound:
    """An adapter that records what was sent, and can be made to fail."""

    def __init__(self, *, fail_text: bool = False, fail_media: bool = False) -> None:
        self.texts: list[str] = []
        self.media: list[str] = []
        self.fail_text = fail_text
        self.fail_media = fail_media

    async def send_text(self, body: str) -> None:
        if self.fail_text:
            raise OutboundFailed("provider down")
        self.texts.append(body)

    async def send_media(self, asset: MediaAsset) -> None:
        if self.fail_media:
            raise OutboundFailed("provider down")
        self.media.append(asset.clave)

    async def send_buttons(self, body: str, buttons: Sequence[Button]) -> None:
        raise AssertionError("payment methods are not buttons")

    async def send_list(self, body: str, button_text: str, sections: Sequence[ListSection]) -> None:
        raise AssertionError("payment methods are not a list")


def make_context(
    db: Session, tenant: Tenant, conversation: Conversation, **overrides: Any
) -> ToolContext:
    config = FactsConfig(options=SHOP.resolve())
    facts = FactsLoader(db, tenant.id, config).load(conversation)
    values: dict[str, Any] = {
        "session": db,
        "tenant_id": tenant.id,
        "llm": MockLLMProvider(),
        "conversation_id": conversation.id,
        "options": SHOP.resolve(),
        "sandbox": True,
        "facts": facts,
        "adapter": RecordingOutbound(),
    }
    values.update(overrides)
    return ToolContext(**values)


async def add_product(ctx: ToolContext, product: Product) -> Order:
    await gestionar_pedido(ctx, {"accion": "agregar", "producto_id": str(product.id)})
    assert ctx.conversation_id is not None
    order = OrderRepository(ctx.session, ctx.tenant_id).active_for_conversation(ctx.conversation_id)
    assert order is not None
    return order


async def send(ctx: ToolContext) -> ToolResult:
    return await enviar_metodos_pago(ctx, {})


def persist(db: Session, tenant: Tenant, conversation: Conversation, ctx: ToolContext) -> None:
    """What the pipeline does with the tools' facts: save them with the stage."""
    states = StateRepo(db, tenant.id)
    state = states.get_or_create(conversation, SHOP)
    states.save(
        conversation.id,
        stage=Stage(state.stage_key),
        funnel=FunnelCode(state.funnel_code),
        facts_patch=ctx.facts_patch,
        reason="turno",
        expected_version=state.version,
    )


class TestWhatIsSent:
    async def test_the_active_methods_go_as_one_text_followed_by_their_qr(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        qr = asset(db, tenant, "qr_yape")
        method(
            db, tenant, "yape", "Yape al 987 000 111 a nombre de Tienda SAC", media_asset_id=qr.id
        )
        method(db, tenant, "transferencia", "BCP 191-1234567-0-12 a nombre de Tienda SAC")
        method(db, tenant, "plin", "Plin al 987 000 222", is_active=False)
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)

        result = await send(ctx)

        assert result.status == "hecho"
        assert len(outbound.texts) == 1
        text = outbound.texts[0]
        assert "Yape al 987 000 111 a nombre de Tienda SAC" in text
        assert "BCP 191-1234567-0-12" in text
        assert "Plin" not in text
        assert outbound.media == ["qr_yape"]
        assert result.data["metodos"] == ["yape", "transferencia"]
        assert result.data["imagenes_enviadas"] == 1
        assert ctx.budget.spent == 2
        assert "S/ 120.00" in (result.decir or "")
        assert ctx.facts_patch["recursos_enviados"] == ["qr_yape"]

    async def test_the_facts_record_the_order_and_the_moment(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        ctx = make_context(db, tenant, conversation)
        order = await add_product(ctx, product)
        before = datetime.now(UTC)

        await send(ctx)

        record = ctx.facts_patch["metodos_pago_enviados"]
        assert record["order_id"] == str(order.id)
        assert before <= datetime.fromisoformat(record["en"]) <= datetime.now(UTC)

    async def test_without_a_qr_the_generic_payment_image_goes_instead(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        asset(db, tenant, "metodos_pago")
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)

        await send(ctx)

        assert outbound.media == ["metodos_pago"]

    async def test_an_inactive_image_is_not_sent(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        qr = asset(db, tenant, "qr_off", is_active=False)
        method(db, tenant, "yape", "Yape al 987 000 111", media_asset_id=qr.id)
        asset(db, tenant, "metodos_pago", is_active=False)
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)

        result = await send(ctx)

        assert outbound.media == []
        assert result.status == "hecho"
        assert ctx.budget.spent == 1

    async def test_without_any_image_only_the_text_goes(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "efectivo", "Paga en tienda")
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)

        result = await send(ctx)

        assert (len(outbound.texts), outbound.media) == (1, [])
        assert result.data["imagenes_enviadas"] == 0
        assert "recursos_enviados" not in ctx.facts_patch

    async def test_two_methods_sharing_a_qr_send_it_once(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        qr = asset(db, tenant, "qr_comun")
        method(db, tenant, "yape", "Yape", media_asset_id=qr.id)
        method(db, tenant, "plin", "Plin", media_asset_id=qr.id)
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)

        await send(ctx)

        assert outbound.media == ["qr_comun"]

    def test_the_text_is_the_owners_wording_and_stays_in_whatsapps_limit(self) -> None:
        rows = [
            PaymentMethodBusiness(method="yape", instructions="  Yape al 987  "),
            PaymentMethodBusiness(method="link", instructions="x" * 5000),
        ]

        text = methods_text(rows)

        assert "• *Yape*\nYape al 987" in text
        assert "*Link de pago*" in text
        assert len(text) <= 4000


class TestOnceForEachOrder:
    async def test_the_second_call_for_the_same_order_is_omitted(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        first_ctx = make_context(db, tenant, conversation)
        await add_product(first_ctx, product)
        await send(first_ctx)
        persist(db, tenant, conversation, first_ctx)
        outbound = RecordingOutbound()

        second = await send(make_context(db, tenant, conversation, adapter=outbound))

        assert second.status == "omitido"
        assert outbound.texts == [] and outbound.media == []

    async def test_two_calls_in_one_turn_send_once(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)

        first = await send(ctx)
        second = await send(ctx)

        assert (first.status, second.status) == ("hecho", "omitido")
        assert len(outbound.texts) == 1

    async def test_a_resend_is_allowed_after_a_rejection_and_only_once(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        ctx = make_context(db, tenant, conversation)
        order = await add_product(ctx, product)
        await send(ctx)
        # The methods went a minute ago; the operator rejects the proof just now.
        ctx.facts_patch["metodos_pago_enviados"]["en"] = (
            datetime.now(UTC) - timedelta(minutes=1)
        ).isoformat()
        persist(db, tenant, conversation, ctx)

        # The operator rejects the proof the customer sent.
        PaymentProofRepository(db, tenant.id).add(
            order_id=order.id,
            conversation_id=conversation.id,
            storage_key="proofs/x.jpg",
            estado="rechazado",
            reviewed_at=datetime.now(UTC),
        )
        order.rechazo_vigente = True
        db.flush()

        again = make_context(db, tenant, conversation)
        assert again.facts.metodos_pago_enviados is False
        resent = await send(again)
        persist(db, tenant, conversation, again)

        assert resent.status == "hecho"
        third = await send(make_context(db, tenant, conversation))
        assert third.status == "omitido"

    async def test_a_new_order_after_a_closed_one_gets_the_methods_again(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        ctx = make_context(db, tenant, conversation)
        first = await add_product(ctx, product)
        await send(ctx)
        persist(db, tenant, conversation, ctx)
        first.estado = "confirmado"
        db.flush()

        new_ctx = make_context(db, tenant, conversation)
        second = await add_product(new_ctx, product)
        assert second.id != first.id
        fresh = make_context(db, tenant, conversation)
        assert fresh.facts.metodos_pago_enviados is False
        outbound = RecordingOutbound()
        result = await send(make_context(db, tenant, conversation, adapter=outbound))

        assert result.status == "hecho"
        assert len(outbound.texts) == 1

    async def test_the_record_of_a_previous_order_does_not_silence_the_new_one_this_turn(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape al 987 000 111")
        ctx = make_context(db, tenant, conversation)
        first = await add_product(ctx, product)
        await send(ctx)
        persist(db, tenant, conversation, ctx)
        first.estado = "confirmado"
        db.flush()

        # The turn starts with the first order as the active one: its facts say "sent".
        turn_ctx = make_context(db, tenant, conversation)
        assert turn_ctx.facts.metodos_pago_enviados is True
        await add_product(turn_ctx, product)  # ...and the customer starts a new order

        result = await send(turn_ctx)

        assert result.status == "hecho"


class TestWhenItCannotBeSent:
    async def test_without_an_order_there_is_nothing_to_pay(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        method(db, tenant, "yape", "Yape")
        outbound = RecordingOutbound()

        result = await send(make_context(db, tenant, conversation, adapter=outbound))

        assert result.status == "no_disponible"
        assert outbound.texts == []

    async def test_an_order_already_in_review_does_not_get_them(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape")
        ctx = make_context(db, tenant, conversation)
        order = await add_product(ctx, product)
        order.estado = "por_confirmar"
        db.flush()

        assert (await send(ctx)).status == "no_disponible"

    async def test_a_business_without_methods_hands_over_instead_of_inventing_accounts(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        ctx = make_context(db, tenant, conversation)
        await add_product(ctx, product)

        result = await send(ctx)

        assert result.status == "no_disponible"
        assert "derivar_a_humano" in (result.decir or "")
        assert "metodos_pago_enviados" not in ctx.facts_patch

    async def test_a_turn_that_cannot_send_says_so(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape")
        ctx = make_context(db, tenant, conversation, adapter=None)
        await add_product(ctx, product)

        result = await send(ctx)

        assert result.status == "no_disponible"
        assert "metodos_pago_enviados" not in ctx.facts_patch

    async def test_with_the_budget_spent_nothing_is_sent_or_recorded(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape")
        outbound = RecordingOutbound()
        ctx = make_context(db, tenant, conversation, adapter=outbound)
        await add_product(ctx, product)
        ctx.budget.consume()
        ctx.budget.consume()

        result = await send(ctx)

        assert result.status == "no_disponible"
        assert "próximo mensaje" in (result.decir or "")
        assert outbound.texts == []
        assert "metodos_pago_enviados" not in ctx.facts_patch

    async def test_with_room_for_the_text_only_the_image_is_left_for_later(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        qr = asset(db, tenant, "qr_yape")
        method(db, tenant, "yape", "Yape", media_asset_id=qr.id)
        outbound = RecordingOutbound()
        ctx = make_context(
            db, tenant, conversation, adapter=outbound, budget=TurnBudget(max=2, reserva_texto=1)
        )
        await add_product(ctx, product)

        result = await send(ctx)

        assert result.status == "hecho"
        assert (len(outbound.texts), outbound.media) == (1, [])
        assert result.data["imagenes_pendientes"] == 1
        assert "tu próximo mensaje" in (result.decir or "")
        assert "metodos_pago_enviados" in ctx.facts_patch

    async def test_if_the_text_fails_nothing_is_recorded(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        method(db, tenant, "yape", "Yape")
        ctx = make_context(db, tenant, conversation, adapter=RecordingOutbound(fail_text=True))
        await add_product(ctx, product)

        result = await send(ctx)

        assert result.status == "error"
        assert ctx.budget.spent == 0
        assert "metodos_pago_enviados" not in ctx.facts_patch

    async def test_if_only_the_image_fails_the_text_still_counts(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        product: Product,
    ) -> None:
        qr = asset(db, tenant, "qr_yape")
        method(db, tenant, "yape", "Yape", media_asset_id=qr.id)
        ctx = make_context(db, tenant, conversation, adapter=RecordingOutbound(fail_media=True))
        await add_product(ctx, product)

        result = await send(ctx)

        assert result.status == "hecho"
        assert result.data["imagenes_pendientes"] == 1
        assert "recursos_enviados" not in ctx.facts_patch
        assert "metodos_pago_enviados" in ctx.facts_patch

    async def test_without_a_conversation_there_is_nothing_to_send_to(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        none = make_context(db, tenant, conversation, conversation_id=None)
        missing = make_context(db, tenant, conversation, conversation_id=uuid.uuid4())

        assert (await send(none)).status == "error"
        assert (await send(missing)).status == "error"


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


class TestTheFunnel:
    async def test_the_funnel_moves_to_accounts_sent(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        product: Product,
    ) -> None:
        """A complete order with the customer's data is at COBRO; sending the
        methods makes the funnel CUENTAS_ENVIADAS, and the sandbox shows the rows."""
        method(db, tenant, "yape", "Yape al 987 000 111")
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content="quiero pagar",
                provider_message_id=f"wamid.{uuid.uuid4().hex}",
            )
        )
        db.flush()
        seed = make_context(db, tenant, conversation)
        await add_product(seed, product)
        states = StateRepo(db, tenant.id)
        state = states.get_or_create(conversation, SHOP)
        states.save(
            conversation.id,
            stage=Stage.INICIO,
            funnel=FunnelCode.NUEVO,
            facts_patch={"saludo_enviado": True},
            reason="turno",
            expected_version=state.version,
        )
        llm = MockLLMProvider(
            responses=[
                ChatResult(content="", tool_calls=(_call("enviar_metodos_pago"),)),
                ChatResult(content="Listo, te los envié. Mándame la captura cuando pagues."),
            ]
        )
        pipeline = TurnPipeline(
            db,
            tenant.id,
            llm=llm,
            outbound=SandboxOutbound(db, tenant.id, conversation.id),
            sandbox=True,
            process_config=ProcessConfigService(db, cache=ProcessConfigCache()),
        )

        result = await pipeline.run(conversation.id, InboundTurn(text="quiero pagar"))

        assert result.stage_after is Stage.COBRO
        assert result.funnel is FunnelCode.CUENTAS_ENVIADAS
        saved = StateRepo(db, tenant.id).get(conversation.id)
        assert saved is not None and saved.funnel_code == "CUENTAS_ENVIADAS"
        assert saved.facts["metodos_pago_enviados"]["order_id"]
        rows = (
            db.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id, Message.direction == "outbound"
                )
            )
            .scalars()
            .all()
        )
        assert any("Yape al 987 000 111" in row.content for row in rows)
        assert funnel_for(SHOP, Stage.COBRO, result.facts) is FunnelCode.CUENTAS_ENVIADAS
