"""Payment proofs through the turn pipeline (Feature 004, TASK-429, C-08).

The verification of the task, with a simulated vision model (nothing here calls a
real one): a valid receipt, a photo that is not a receipt, a different amount, a
repeated code and a timeout; a receipt that arrives during a handover is recorded
and announced but the bot does not answer; the stage moves to VERIFICACION; and the
model is told the result in the state block.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import IMAGE_UNREADABLE_LINE, InboundTurn, TurnPipeline
from app.models.business import AgentConfig, BusinessProfile, Product
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationStageEvent, FunnelCode
from app.models.orders import Order, PaymentProof
from app.models.tenant import Tenant
from app.orders.proofs import ImageReader, InboundImage, ProofPipeline, ReceivedImage
from app.orders.service import OrderService
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.state import StateRepo
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider
from app.providers.llm.base import LLMError
from app.providers.storage.mock import MockStorageProvider
from app.repositories.orders import PaymentProofRepository
from app.services.turn_outbound import SandboxOutbound

D = Decimal
SHOP = TEMPLATES["productos"]
PHOTO = ReceivedImage(data=b"fake-jpeg-bytes", mime_type="image/jpeg")
# The account holder the test business collects under.
PAYEE = "Tienda SAC"


def today_in_lima() -> str:
    return datetime.now(ZoneInfo("America/Lima")).date().isoformat()


def receipt(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "es_comprobante": True,
        "monto": "120.00",
        "moneda": "PEN",
        "fecha": today_in_lima(),
        "codigo": "OP-777",
        "entidad": "Yape",
        "destinatario": "Tienda SAC",
        "confianza": 0.95,
    }
    data.update(overrides)
    return data


class FakeNotices:
    def __init__(self) -> None:
        self.proofs: list[uuid.UUID] = []

    def new_proof(self, order: Order, proof: PaymentProof) -> None:
        self.proofs.append(proof.id)


class FakeReader:
    """Returns the photo, or None to simulate a download that failed."""

    def __init__(self, image: ReceivedImage | None = PHOTO) -> None:
        self.image = image

    async def read(self, image: InboundImage) -> ReceivedImage | None:
        return self.image


def vision(*answers: dict[str, Any] | Exception) -> MockLLMProvider:
    llm = MockLLMProvider(responses=[ChatResult(content="Tu comprobante está en verificación.")])
    llm.vision_responses.extend(answers)
    return llm


def the_prompt(llm: MockLLMProvider) -> str:
    return "\n".join(m.content for m in llm.chat_calls[0].messages if m.role == "system")


class World:
    """A tenant with a digital product and a customer at the point of paying."""

    def __init__(self, db: Session, *, sandbox: bool = True) -> None:
        self.db = db
        self.tenant = Tenant(name=f"Tienda {uuid.uuid4().hex[:6]}")
        db.add(self.tenant)
        db.flush()
        db.add_all(
            [
                BusinessProfile(
                    tenant_id=self.tenant.id, business_name="Tienda", industry="retail"
                ),
                AgentConfig(tenant_id=self.tenant.id, agent_name="Leo"),
            ]
        )
        self.product = Product(
            tenant_id=self.tenant.id, name="Curso", price=D("120.00"), es_digital=True
        )
        db.add(self.product)
        db.flush()
        self.contact = Contact(
            tenant_id=self.tenant.id,
            phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}",
            custom_data={"nombres": "Ana"},
            last_inbound_at=datetime.now(UTC),
        )
        db.add(self.contact)
        db.flush()
        self.storage = MockStorageProvider()
        self.notices = FakeNotices()
        self.sandbox = sandbox
        self.new_chat(sandbox=sandbox)

    def new_chat(self, *, sandbox: bool) -> Conversation:
        """Another conversation of the same customer, with an order at COBRO."""
        self.sandbox = sandbox
        self.conversation = Conversation(
            tenant_id=self.tenant.id,
            channel="sandbox" if sandbox else "whatsapp",
            contact_id=self.contact.id,
        )
        self.db.add(self.conversation)
        self.db.flush()
        self.order = self._order()
        return self.conversation

    def _order(self) -> Order:
        service = OrderService(self.db, self.tenant.id)
        order = service.open_draft(
            self.conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=self.sandbox
        )
        service.add_product(order, self.product)
        states = StateRepo(self.db, self.tenant.id)
        state = states.get_or_create(self.conversation, SHOP)
        states.save(
            self.conversation.id,
            stage=Stage.INICIO,
            funnel=FunnelCode.NUEVO,
            facts_patch={"saludo_enviado": True},
            reason="turno",
            expected_version=state.version,
        )
        return order

    def say(self, text: str = "ya pagué") -> Message:
        message = Message(
            tenant_id=self.tenant.id,
            conversation_id=self.conversation.id,
            direction="inbound",
            author="customer",
            content=text,
            msg_type="image",
            provider_message_id=f"wamid.{uuid.uuid4().hex}",
        )
        self.db.add(message)
        self.db.flush()
        return message

    def pipeline(
        self,
        llm: MockLLMProvider,
        reader: ImageReader | None = None,
        payees: tuple[str, ...] = (PAYEE,),
    ) -> TurnPipeline:
        return TurnPipeline(
            self.db,
            self.tenant.id,
            llm=llm,
            outbound=SandboxOutbound(self.db, self.tenant.id, self.conversation.id),
            sandbox=self.sandbox,
            process_config=ProcessConfigService(self.db, cache=ProcessConfigCache()),
            proofs=ProofPipeline(
                self.db,
                self.tenant.id,
                llm=llm,
                storage=self.storage,
                notices=self.notices,
                payees=payees,
            ),
            images=reader or FakeReader(),
        )

    async def send_photo(
        self,
        llm: MockLLMProvider,
        reader: ImageReader | None = None,
        text: str = "ya pagué",
        payees: tuple[str, ...] = (PAYEE,),
    ) -> Any:
        message = self.say(text)
        return await self.pipeline(llm, reader, payees).run(
            self.conversation.id,
            InboundTurn(image=True, images=(InboundImage(message_id=message.id),)),
        )

    def stored_proofs(self) -> list[PaymentProof]:
        return list(PaymentProofRepository(self.db, self.tenant.id).for_order(self.order.id))


@pytest.fixture
def world(db: Session) -> World:
    return World(db)


class TestAValidReceipt:
    async def test_it_is_recorded_the_order_waits_and_the_stage_is_verification(
        self, db: Session, world: World
    ) -> None:
        result = await world.send_photo(vision(receipt()))

        [proof] = world.stored_proofs()
        assert proof.estado == "por_verificar"
        assert (proof.monto, proof.moneda, proof.codigo_operacion) == (D("120.00"), "PEN", "OP-777")
        assert proof.entidad == "Yape" and proof.confianza == D("0.95")
        assert proof.observaciones == []
        assert proof.extraccion is not None and proof.extraccion["legible"] is True
        db.refresh(world.order)
        assert world.order.estado == "por_confirmar"
        assert world.order.datos_cliente is not None
        assert world.order.datos_cliente["nombres"] == "Ana"
        assert result.stage_before is Stage.INICIO
        assert result.stage_previa is Stage.COBRO
        assert result.stage_after is Stage.VERIFICACION
        assert result.funnel is FunnelCode.EN_VERIFICACION
        # The image is in private storage, under the tenant.
        assert proof.storage_key.startswith(f"tenants/{world.tenant.id}/proofs/")
        assert world.storage.get(proof.storage_key) == PHOTO.data

    async def test_the_event_says_a_proof_moved_the_stage(self, db: Session, world: World) -> None:
        await world.send_photo(vision(receipt()))

        events = (
            db.execute(
                select(ConversationStageEvent).where(
                    ConversationStageEvent.conversation_id == world.conversation.id
                )
            )
            .scalars()
            .all()
        )
        assert [(e.to_stage, e.reason) for e in events] == [("VERIFICACION", "comprobante")]

    async def test_the_model_is_told_in_the_state_block_and_never_that_it_is_confirmed(
        self, world: World
    ) -> None:
        llm = vision(receipt())

        await world.send_photo(llm)

        prompt = the_prompt(llm)
        assert "ESTE TURNO" in prompt
        assert "comprobante recibido: S/ 120.00, Yape, op. OP-777" in prompt
        assert "no digas que está confirmado" in prompt
        assert "Pago: por verificar" in prompt
        assert "ETAPA ACTUAL: VERIFICACION" in prompt


class TestWhatLooksOff:
    async def test_a_different_amount_is_flagged_not_rejected(self, world: World) -> None:
        await world.send_photo(vision(receipt(monto="100.00")))

        [proof] = world.stored_proofs()
        assert proof.observaciones == ["monto_distinto"]
        assert proof.estado == "por_verificar"

    async def test_a_different_currency_counts_as_a_different_amount(self, world: World) -> None:
        await world.send_photo(vision(receipt(moneda="USD")))

        assert world.stored_proofs()[0].observaciones == ["monto_distinto"]

    async def test_low_confidence_is_flagged(self, world: World) -> None:
        await world.send_photo(vision(receipt(confianza=0.4)))

        assert world.stored_proofs()[0].observaciones == ["baja_confianza"]

    async def test_a_repeated_code_is_flagged_among_real_orders_of_the_same_tenant(
        self, db: Session
    ) -> None:
        mine = World(db, sandbox=False)
        await mine.send_photo(vision(receipt(codigo="OP-DUP")))
        assert mine.stored_proofs()[0].observaciones == []

        mine.new_chat(sandbox=False)
        await mine.send_photo(vision(receipt(codigo="op-dup")))
        assert mine.stored_proofs()[0].observaciones == ["codigo_repetido"]

        # Another tenant's code is nobody's business.
        other = World(db, sandbox=False)
        await other.send_photo(vision(receipt(codigo="OP-DUP")))
        assert other.stored_proofs()[0].observaciones == []

    async def test_a_simulator_proof_never_counts_against_a_real_one(self, db: Session) -> None:
        shop = World(db, sandbox=True)
        await shop.send_photo(vision(receipt(codigo="OP-S")))
        assert shop.stored_proofs()[0].observaciones == []

        shop.new_chat(sandbox=False)
        await shop.send_photo(vision(receipt(codigo="OP-S")))
        assert shop.stored_proofs()[0].observaciones == []

        # ...while the simulator can still show the flag against its own.
        shop.new_chat(sandbox=True)
        await shop.send_photo(vision(receipt(codigo="OP-S")))
        assert shop.stored_proofs()[0].observaciones == ["codigo_repetido"]


class TestAnEmptyListMeansEverythingWasChecked:
    """Milestone B, findings D-1 and D-2."""

    async def test_a_negative_amount_is_flagged_and_never_matches_the_total(
        self, world: World
    ) -> None:
        await world.send_photo(vision(receipt(monto="-120.00")))

        [proof] = world.stored_proofs()
        assert proof.monto == D("-120.00")
        assert proof.observaciones == ["monto_negativo", "monto_distinto"]

    async def test_an_amount_with_a_decimal_comma_matches_the_total(self, world: World) -> None:
        await world.send_photo(vision(receipt(monto="120,00")))

        assert world.stored_proofs()[0].observaciones == []

    async def test_without_accounts_to_compare_the_recipient_is_not_verified(
        self, world: World
    ) -> None:
        await world.send_photo(vision(receipt()), payees=())

        assert world.stored_proofs()[0].observaciones == ["destinatario_no_verificado"]

    async def test_a_payment_to_someone_else_is_flagged(self, world: World) -> None:
        await world.send_photo(vision(receipt(destinatario="Carlos Quispe")))

        assert world.stored_proofs()[0].observaciones == ["destinatario_distinto"]

    async def test_an_unreadable_recipient_is_not_verified(self, world: World) -> None:
        await world.send_photo(vision(receipt(destinatario=None)))

        assert world.stored_proofs()[0].observaciones == ["destinatario_no_verificado"]

    @pytest.mark.parametrize("shown", ["TIENDA SAC", "Tienda S*", "tienda", "Tiénda  S.A.C"])
    async def test_a_shortened_or_masked_holder_name_still_matches(
        self, world: World, shown: str
    ) -> None:
        await world.send_photo(vision(receipt(destinatario=shown)))

        assert world.stored_proofs()[0].observaciones == []

    async def test_an_operation_dated_before_the_order_is_flagged(self, world: World) -> None:
        await world.send_photo(vision(receipt(fecha="2020-01-01")))

        assert world.stored_proofs()[0].observaciones == ["fecha_anterior_al_pedido"]


class TestAReceiptForAnIncompleteOrder:
    """Milestone B, finding E-1: the proof waits on the draft, nobody is told yet."""

    def proofs(self, world: World, llm: MockLLMProvider) -> ProofPipeline:
        return ProofPipeline(
            world.db,
            world.tenant.id,
            llm=llm,
            storage=world.storage,
            notices=world.notices,
            payees=(PAYEE,),
        )

    async def test_the_proof_waits_and_the_model_asks_for_what_is_missing(
        self, db: Session, world: World
    ) -> None:
        world.contact.custom_data = {}
        db.flush()

        outcome = await self.proofs(world, vision(receipt())).handle(world.conversation, PHOTO)

        [proof] = world.stored_proofs()
        db.refresh(world.order)
        assert outcome.kind == "registrado" and outcome.proof is proof
        assert proof.estado == "por_verificar"
        assert world.order.estado == "borrador"
        assert world.notices.proofs == []
        assert outcome.context is not None
        assert "falta: nombres" in outcome.context
        assert "no digas que está en revisión" in outcome.context

    async def test_another_photo_while_it_waits_is_neither_a_proof_nor_a_notice(
        self, db: Session, world: World
    ) -> None:
        world.contact.custom_data = {}
        db.flush()
        llm = vision(receipt(), receipt(codigo="OP-888"))
        pipeline = self.proofs(world, llm)
        await pipeline.handle(world.conversation, PHOTO)

        outcome = await pipeline.handle(world.conversation, PHOTO)

        assert outcome.kind == "ignorado"
        assert outcome.context is not None and "ya tenemos su comprobante" in outcome.context
        assert len(world.stored_proofs()) == 1
        assert world.notices.proofs == []
        # The second photo was not even read.
        assert len(llm.vision_responses) == 1

    async def test_once_the_order_is_complete_it_goes_to_the_operator_and_only_then_is_told(
        self, db: Session
    ) -> None:
        world = World(db, sandbox=False)
        world.contact.custom_data = {}
        db.flush()
        pipeline = self.proofs(world, vision(receipt()))
        await pipeline.handle(world.conversation, PHOTO)
        assert pipeline.submit_waiting(world.conversation) is None

        world.contact.custom_data = {"nombres": "Ana"}
        db.flush()
        order = pipeline.submit_waiting(world.conversation)

        [proof] = world.stored_proofs()
        assert order is not None and order.estado == "por_confirmar"
        assert order.datos_cliente is not None and order.datos_cliente["nombres"] == "Ana"
        assert world.notices.proofs == [proof.id]
        # Nothing waits any more: a second call does nothing.
        assert pipeline.submit_waiting(world.conversation) is None
        assert world.notices.proofs == [proof.id]

    async def test_the_turn_that_completes_the_order_moves_it_to_verification(
        self, db: Session, world: World
    ) -> None:
        world.contact.custom_data = {}
        db.flush()
        await self.proofs(world, vision(receipt())).handle(world.conversation, PHOTO)
        # The customer gave their name (a tool would write it during the turn).
        world.contact.custom_data = {"nombres": "Ana"}
        db.flush()
        llm = vision()
        world.say("me llamo Ana")

        result = await world.pipeline(llm).run(world.conversation.id, InboundTurn(text="Ana"))

        assert result.outcome == "answered", result.gate
        db.refresh(world.order)
        assert world.order.estado == "por_confirmar"
        assert result.stage_after is Stage.VERIFICACION
        # The model of that turn knew the receipt was already in.
        assert "comprobante ya recibido" in the_prompt(llm)


class TestWhatIsNotAReceipt:
    async def test_a_photo_that_is_not_a_receipt_creates_nothing_and_the_model_knows(
        self, db: Session, world: World
    ) -> None:
        llm = vision({"es_comprobante": False})

        result = await world.send_photo(llm)

        assert world.stored_proofs() == []
        assert world.storage.objects == {}
        db.refresh(world.order)
        assert world.order.estado == "borrador"
        assert result.stage_after is Stage.COBRO
        assert "la imagen NO es un comprobante" in the_prompt(llm)
        assert world.notices.proofs == []

    async def test_an_image_outside_the_payment_stage_is_only_context(
        self, db: Session, world: World
    ) -> None:
        """Not at COBRO: the vision model is not even called."""
        world.order.estado = "descartado"
        db.flush()
        llm = vision(receipt())

        await world.send_photo(llm)

        assert llm.vision_calls == []
        assert "[El cliente envió una imagen]" in the_prompt(llm)

    async def test_cash_on_delivery_has_no_proof(self, db: Session, world: World) -> None:
        world.order.modalidad_pago = "contra_entrega"
        db.flush()
        llm = vision(receipt())

        await world.send_photo(llm)

        assert world.stored_proofs() == []
        assert "[El cliente envió una imagen]" in the_prompt(llm)


class TestWhenVisionFails:
    @pytest.mark.parametrize("failure", [LLMError("sin visión"), TimeoutError()])
    async def test_the_proof_is_kept_without_data_for_the_operator_to_read(
        self, world: World, failure: Exception
    ) -> None:
        llm = vision(failure)

        result = await world.send_photo(llm)

        [proof] = world.stored_proofs()
        assert proof.observaciones == ["sin_datos", "destinatario_no_verificado"]
        assert proof.monto is None and proof.codigo_operacion is None
        assert proof.extraccion is not None and proof.extraccion["legible"] is False
        assert result.stage_after is Stage.VERIFICACION
        assert "no se pudo leer" in the_prompt(llm)

    async def test_a_slow_model_is_cut_at_the_limit(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.orders.vision.VISION_TIMEOUT_SECONDS", 0.05)
        llm = vision(receipt())
        llm.vision_delay = 0.5

        await world.send_photo(llm)

        assert world.stored_proofs()[0].observaciones == ["sin_datos", "destinatario_no_verificado"]

    async def test_a_photo_that_cannot_be_downloaded_asks_for_it_again(self, world: World) -> None:
        llm = vision(receipt())

        await world.send_photo(llm, FakeReader(None))

        assert world.stored_proofs() == []
        assert IMAGE_UNREADABLE_LINE in the_prompt(llm)


class TestDuringAHandover:
    async def test_the_receipt_is_recorded_and_announced_but_the_bot_stays_quiet(
        self, db: Session, world: World
    ) -> None:
        states = StateRepo(db, world.tenant.id)
        state = states.get(world.conversation.id)
        assert state is not None
        states.save(
            world.conversation.id,
            stage=Stage(state.stage_key),
            funnel=FunnelCode(state.funnel_code),
            facts_patch={
                "derivado": {
                    "motivo": "pide_humano",
                    "hasta": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
                }
            },
            reason="turno",
            expected_version=state.version,
        )
        llm = vision(receipt())

        result = await world.send_photo(llm)

        assert result.outcome == "gated" and result.gate == "handover"
        assert llm.chat_calls == []
        assert len(world.stored_proofs()) == 1
        assert result.stage_after is Stage.VERIFICACION
        db.refresh(world.order)
        assert world.order.estado == "por_confirmar"

    async def test_the_business_is_told_of_a_real_receipt_even_when_the_bot_is_paused(
        self, db: Session
    ) -> None:
        real = World(db, sandbox=False)
        llm = vision(receipt())

        result = await real.send_photo(llm)

        assert result.outcome == "gated"
        assert llm.chat_calls == []
        [proof] = real.stored_proofs()
        assert real.notices.proofs == [proof.id]

    async def test_the_simulator_tells_nobody(self, world: World) -> None:
        await world.send_photo(vision(receipt()))

        assert len(world.stored_proofs()) == 1
        assert world.notices.proofs == []


class TestAfterARejection:
    async def test_a_new_receipt_is_read_again_while_the_payment_is_rejected(
        self, db: Session, world: World
    ) -> None:
        await world.send_photo(vision(receipt(monto="50.00")))
        # The operator rejects: proof rejected, order back to draft, rejection in force.
        [first] = world.stored_proofs()
        first.estado = "rechazado"
        OrderService(db, world.tenant.id).transition(
            world.order, "borrador", motivo="monto no coincide", rechazo_vigente=True
        )
        db.flush()

        result = await world.send_photo(vision(receipt()), text="aquí está")

        proofs = world.stored_proofs()
        assert {p.estado for p in proofs} == {"rechazado", "por_verificar"}
        assert result.stage_previa is Stage.VERIFICACION
        assert result.stage_after is Stage.VERIFICACION
        db.refresh(world.order)
        assert world.order.estado == "por_confirmar"

    async def test_a_second_image_while_waiting_is_not_read(self, world: World) -> None:
        await world.send_photo(vision(receipt()))
        llm = vision(receipt())

        await world.send_photo(llm, text="otra foto")

        assert llm.vision_calls == []
        assert len(world.stored_proofs()) == 1
