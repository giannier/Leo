"""The turn pipeline: one customer turn in, state and messages out (Feature 004, TASK-421).

Replaces "one big prompt plus every tool" with the 14 steps of `plan.md`:

     1  load      conversation, contact, effective configuration (cached 60 s)
     2  facts     stored + derived
     3  previa    the stage as if nobody had taken the conversation
     4  inbound   the ad, the tapped option, the image
     5  reload    facts again, because step 4 may have written some
     6  stage     computed, and saved with its event when it changed
     7  gate      assistant paused, opt-out, handover in force: save and stay quiet
     8  prompt    the sections of the stage (`prompts/stage.py`)
     9  tools     only the stage's, plus the global ones, and the message budget
    10  llm       `AgentCore`, which only runs the loop
    11  format    one or two bubbles, no model involved
    12  send      the tools already sent their files and options; the text goes last
    13  after     greeting recorded, stage recomputed, event if it changed
    14  trace     stage, funnel, tools offered, messages sent, level

Two decisions run through it.

**The state is saved before the gate.** A handover, a paused assistant or an opt-out
only decide whether the bot *speaks*. The ad the customer came from, the path they
tapped and the stage they are in are still true, and the operator who picks the
conversation up later reads them. A payment proof arriving during a pause (step 4,
TASK-429) must not be lost either, which is why the inbound work happens before the
gate.

**Nothing here knows who is on the other side.** The tools speak through a
`TurnOutbound`: the WhatsApp adapter in production, a recorder in the simulator
(C-19). The pipeline only decides whether the text is stored `queued` (to be sent by
`ReplySender`, which retries and honours the 24-hour window) or just stored.

Step 4 reads payment proofs (`ProofPipeline`, TASK-429): an image that arrives while
the stage (as if nobody had taken the conversation) is COBRO, or VERIFICACION with a
rejected payment, is read, stored and recorded before the model is called, handover
or not. Any other image reaches the model as one line of context, "[El cliente envió
una imagen]".
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.agent.budget import TurnBudget
from app.agent.core import AgentCore, AgentReply, TurnTrace
from app.agent.examples import select_examples
from app.agent.formatter import to_bubbles
from app.agent.model_policy import allowed_levels, effective_level
from app.agent.outbound import TurnOutbound
from app.agent.prompts.stage import build_stage_prompt
from app.agent.situations import SelectedSituation, select_situations
from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.registry import ToolContext
from app.models.conversation import Conversation, Message
from app.models.conversation_state import FunnelCode
from app.models.tenant import Tenant
from app.orders.notices import OrderNotices
from app.orders.proofs import ImageReader, InboundImage, ProofOutcome, ProofPipeline
from app.process.bundle_request import Bundle, requested_bundle
from app.process.engine import compute_stage, funnel_for
from app.process.facts import EntradaAnuncio, Facts, FactsLoader
from app.process.interactive import InteractiveReply, apply_interactive
from app.process.service import EffectiveProcessConfig, ProcessConfigService
from app.process.state import StateConflict, StateRepo, facts_snapshot
from app.process.templates.base import Stage
from app.process.turn import IMAGE_RECEIVED_LINE, TurnContext
from app.providers.llm.base import LLMProvider
from app.providers.whatsapp.base import Referral
from app.repositories.channel import ContactRepository
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.product import ProductRepository
from app.services.pilot import in_pilot
from app.services.trial import agent_may_reply

logger = logging.getLogger(__name__)

GateReason = Literal["paused", "opted_out", "outside_pilot", "handover", "takeover"]
Outcome = Literal["answered", "gated", "unsupported", "llm_down"]

# Why a stage was saved: an ordinary turn, or a payment proof that arrived in it.
REASON_TURN = "turno"
REASON_PROOF = "comprobante"

# What the model is told when a receipt photo could not be opened.
IMAGE_UNREADABLE_LINE = "no se pudo abrir la imagen; pídele que la envíe de nuevo"
# Packages read to recognize one the customer names; a catalogue rarely has more.
_BUNDLES_READ = 50


@dataclass(frozen=True)
class InboundTurn:
    """What the customer did since the last reply: a burst, already read."""

    # The text of the burst (the title of a tapped option counts as text).
    text: str = ""
    # The options tapped, in order, as the webhook reported them.
    interactive: tuple[InteractiveReply, ...] = ()
    # The ad the customer came from, when the first message carried one.
    referral: Referral | None = None
    # An image arrived.
    image: bool = False
    # The image messages of the burst, to fetch if a receipt is expected.
    images: tuple[InboundImage, ...] = ()

    @property
    def readable(self) -> bool:
        """Whether there is anything the assistant can answer."""
        return bool(self.text.strip()) or self.image or bool(self.images) or bool(self.interactive)

    def for_the_model(self) -> str:
        """The user message the model gets: the text, or the image line."""
        return self.text.strip() or (IMAGE_RECEIVED_LINE if self.image else "")


@dataclass
class TurnResult:
    """What the turn did, for the caller and for the simulator's debug panel."""

    outcome: Outcome
    stage_before: Stage
    stage_previa: Stage
    stage_after: Stage
    funnel: FunnelCode
    facts: Facts
    gate: GateReason | None = None
    tools_enabled: tuple[str, ...] = ()
    reply: AgentReply | None = None
    # Files and options the tools sent while the model was working, in order.
    tool_messages: list[Message] = field(default_factory=list)
    # The text bubbles of the answer, in order.
    text_messages: list[Message] = field(default_factory=list)
    model_tier: str | None = None
    # The business's situations that entered the turn (017, TASK-463).
    situations: list[SelectedSituation] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        """Every outbound message of the turn, in the order the customer saw them."""
        return [*self.tool_messages, *self.text_messages]


class TurnPipeline:
    """Runs one turn of one conversation."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        llm: LLMProvider,
        outbound: TurnOutbound | None = None,
        sandbox: bool = False,
        queue_text: bool = False,
        model_tier_applies: bool = True,
        process_config: ProcessConfigService | None = None,
        proofs: ProofPipeline | None = None,
        images: ImageReader | None = None,
        notices: OrderNotices | None = None,
        situation_drafts: Sequence[uuid.UUID] = (),
        llm_open: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        self._outbound = outbound
        self._sandbox = sandbox
        # The real channel stores the text `queued` and `ReplySender` sends it.
        self._queue_text = queue_text
        # A tenant that brings its own model has no intelligence level (C-02).
        self._tier_applies = model_tier_applies
        self._process = process_config or ProcessConfigService(session)
        self._proofs = proofs
        self._images = images
        # Where the business is told what waits for a person (TASK-431).
        self._notices = notices
        # Drafts the simulator tries in this turn as if active (017, RF-009).
        self._situation_drafts = tuple(situation_drafts) if sandbox else ()
        # The tenant's circuit breaker is open (TASK-445): no model this turn.
        self._llm_open = llm_open
        self._clock = clock
        self._states = StateRepo(session, tenant_id)
        self._messages = MessageRepository(session, tenant_id)

    # --------------------------------------------------------------------- run

    async def run(self, conversation_id: uuid.UUID, inbound: InboundTurn) -> TurnResult:
        now = self._clock()

        # 1-2. load
        conversation = ConversationRepository(self._session, self._tenant_id).get(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id!s} not found for this tenant")
        config = self._process.effective(self._tenant_id)
        template = config.template
        state = self._states.get_or_create(conversation, template)
        loader = FactsLoader(self._session, self._tenant_id, config.facts_config())
        facts = loader.load(conversation, now=now)
        stage_before = Stage(state.stage_key)
        funnel_before = FunnelCode(state.funnel_code)

        # 3. the stage if nobody had taken the conversation (it decides whether an
        # image is read as a payment proof, also during a pause)
        stage_previa = compute_stage(template, config.options, facts, now, ignorar_derivacion=True)

        # 4-5. inbound
        patch, turn = self._read_inbound(inbound, facts, config, now)
        proof_made = False
        if inbound.images and self._expects_proof(stage_previa, facts):
            # A receipt that arrives while the bot is quiet (paused, handed over,
            # opted out) is stored for a person without reaching any model (P-1).
            quiet = self._gate(conversation, facts, now) is not None
            turn, proof_made = await self._read_proofs(
                conversation, inbound.images, turn, read=not quiet
            )
        version = state.version
        if patch:
            version = self._save(conversation_id, stage_before, funnel_before, patch, version)
        if patch or proof_made:
            facts = loader.load(conversation, now=now)

        # 6. stage
        stage = compute_stage(template, config.options, facts, now)
        funnel = funnel_for(template, stage, facts)
        if (stage, funnel) != (stage_before, funnel_before):
            version = self._save(
                conversation_id,
                stage,
                funnel,
                None,
                version,
                snapshot=facts_snapshot(facts),
                reason=REASON_PROOF if proof_made else REASON_TURN,
            )

        def result(outcome: Outcome, **extra: Any) -> TurnResult:
            return TurnResult(
                outcome=outcome,
                stage_before=stage_before,
                stage_previa=stage_previa,
                stage_after=stage,
                funnel=funnel,
                facts=facts,
                **extra,
            )

        # 7. gate
        gate = self._gate(conversation, facts, now)
        if gate is not None:
            logger.info(
                "turn saved without an answer",
                extra={"tenant_id": str(self._tenant_id), "gate": gate},
            )
            return result("gated", gate=gate)
        if not inbound.readable:
            # A voice note, a sticker, a location: the stage is untouched and the
            # caller answers with the tenant's own "I can only read text".
            return result("unsupported")
        if self._llm_open:
            # After the gates: a paused or handed-over conversation stays quiet
            # rather than getting the fallback.
            return result("llm_down")

        # 8-9. prompt and tools
        budget = TurnBudget()
        registry = registry_for_stage(template, stage, facts, config.options)
        context = ToolContext(
            session=self._session,
            tenant_id=self._tenant_id,
            llm=self._llm,
            conversation_id=conversation_id,
            budget=budget,
            adapter=self._outbound,
            options=config.options,
            sandbox=self._sandbox,
            turn=turn,
            facts=facts,
            notices=None if self._sandbox else self._notices,
            rubro_version_id=config.rubro_version_id,
        )
        situations = await select_situations(
            self._session,
            self._tenant_id,
            stage=stage.value,
            message=inbound.text,
            llm=self._llm,
            max_situaciones=config.behavior.max_situaciones,
            drafts=self._situation_drafts,
        )
        examples = await select_examples(
            self._session, self._tenant_id, message=inbound.text, llm=self._llm
        )
        prompt = build_stage_prompt(
            config,
            facts,
            stage,
            now=now,
            turn=turn,
            tool_specs=registry.specs(),
            situations=situations,
            examples=examples,
        )

        # 10. model
        anchor = self._messages.latest_for_conversation(conversation_id)
        core = AgentCore(self._session, self._tenant_id, llm=self._llm, registry=registry)
        reply = await core.respond(
            conversation_id=conversation_id,
            customer_message=inbound.for_the_model(),
            system_prompt=prompt.text,
            context=context,
        )
        tool_messages = list(
            self._messages.outbound_after(conversation_id, anchor.id if anchor else None)
        )

        # 11-12. format and store the text. The files and options already went out.
        bubbles = to_bubbles(reply.content, config.behavior.detalle, budget)
        if reply.silent and tool_messages:
            bubbles = []
        # The model often writes again the text it just sent as the body of the
        # buttons or the list; the customer would read it twice (TASK-447).
        already = {_same_text(m.content) for m in tool_messages if m.msg_type == "interactive"}
        bubbles = [bubble for bubble in bubbles if _same_text(bubble) not in already]
        text_messages = [
            self._messages.append(
                conversation_id=conversation_id,
                direction="outbound",
                author="agent",
                content=bubble,
                delivery_status="queued" if self._queue_text else None,
            )
            for bubble in bubbles
        ]

        # 13. after: the greeting, whatever the tools wrote, and the stage again
        after: dict[str, Any] = dict(context.facts_patch)
        if not reply.degraded and not facts.saludo_enviado:
            after["saludo_enviado"] = True
        if facts.seguimiento is not None:
            after["seguimiento"] = None
        if after:
            version = self._save(conversation_id, stage, funnel, after, version)
        # A receipt that waited for the order to be complete goes to the operator
        # as soon as this turn's tools completed it (finding E-1).
        submitted = self._submit_waiting_proof(conversation)
        facts_after = loader.load(conversation, now=now)
        stage_after = compute_stage(template, config.options, facts_after, now)
        funnel_after = funnel_for(template, stage_after, facts_after)
        if (stage_after, funnel_after) != (stage, funnel):
            version = self._save(
                conversation_id,
                stage_after,
                funnel_after,
                None,
                version,
                snapshot=facts_snapshot(facts_after),
                reason=REASON_PROOF if submitted else REASON_TURN,
            )

        # 14. trace
        tier = (
            effective_level(
                config.intelligence_level, allowed_levels(self._session, self._tenant_id)
            )
            if self._tier_applies
            else None
        )
        sent = [*tool_messages, *text_messages]
        if sent:
            core.persist_trace(
                message=text_messages[0] if text_messages else sent[-1],
                reply=reply,
                turn=TurnTrace(
                    stage_key=stage_after.value,
                    funnel_code=funnel_after.value,
                    tools_enabled=list(registry.names()),
                    outbound_count=len(sent),
                    model_tier=tier,
                    situaciones=[s.trace() for s in situations] or None,
                ),
            )

        return TurnResult(
            outcome="answered",
            stage_before=stage_before,
            stage_previa=stage_previa,
            stage_after=stage_after,
            funnel=funnel_after,
            facts=facts_after,
            tools_enabled=registry.names(),
            reply=reply,
            tool_messages=tool_messages,
            text_messages=text_messages,
            model_tier=tier,
            situations=situations,
        )

    # ----------------------------------------------------------------- helpers

    def _read_inbound(
        self, inbound: InboundTurn, facts: Facts, config: EffectiveProcessConfig, now: datetime
    ) -> tuple[dict[str, Any], TurnContext]:
        """Step 4: what the customer's messages change.

        The ad becomes a stored fact (`entrada_anuncio`). A tapped option is read
        from the event, not from the text: the text is only the title the customer
        saw. A path is stored even if the model never calls `elegir_camino`; any
        other option becomes one line of context for this turn. A package the
        customer names before there is an order is kept (`paquete_pedido`), so the
        agent builds that one when the topics come, whatever it remembers.
        """
        patch: dict[str, Any] = {}
        if inbound.referral is not None:
            patch["entrada_anuncio"] = _entry_from(inbound.referral, now)
        if inbound.text and facts.pedido is None:
            named = self._named_bundle(inbound.text)
            if named is not None and named != facts.paquete_pedido:
                patch["paquete_pedido"] = named
            if named is not None and facts.camino is None and "camino" not in patch:
                # Asking for a package is choosing to buy: with one sales path,
                # that is the path, as a tapped one is (C-25).
                selling = [c for c in config.options.caminos if c.tipo == "venta"]
                if config.options.calificacion_activa and len(selling) == 1:
                    patch["camino"] = selling[0].clave

        lines: list[str] = []
        view = facts
        for tap in inbound.interactive:
            outcome = apply_interactive(tap, view, config.options)
            patch.update(outcome.facts_patch)
            if outcome.context:
                lines.append(outcome.context)
            if "camino" in outcome.facts_patch:
                view = view.model_copy(update={"camino": outcome.facts_patch["camino"]})

        turn = TurnContext(
            opcion="; ".join(lines) if lines else None,
            imagen_recibida=inbound.image,
        )
        return patch, turn

    def _named_bundle(self, text: str) -> str | None:
        """The live package the customer's words name, if exactly one."""
        products = ProductRepository(self._session, self._tenant_id).search_active(
            solo_paquetes=True, limit=_BUNDLES_READ
        )
        bundles = [Bundle(p.name, p.bundle_size) for p in products if p.bundle_size]
        found = requested_bundle(text, bundles)
        return found.name if found else None

    @staticmethod
    def _expects_proof(stage_previa: Stage, facts: Facts) -> bool:
        """Whether an image now is probably a payment receipt (RF-022): the customer
        is being asked to pay, or to pay again after a rejection."""
        return stage_previa is Stage.COBRO or (
            stage_previa is Stage.VERIFICACION and facts.pago_estado == "rechazado"
        )

    async def _read_proofs(
        self,
        conversation: Conversation,
        images: tuple[InboundImage, ...],
        turn: TurnContext,
        *,
        read: bool = True,
    ) -> tuple[TurnContext, bool]:
        """Step 4, images: run each through the proof pipeline.

        Returns the turn context with what the model should know (a receipt came in,
        or the photo is not one) and whether a proof was recorded. With nothing to
        read them with, the image stays what it was: a line of context.
        """
        if self._proofs is None or self._images is None:
            return turn, False
        lines: list[str] = []
        recorded = False
        for image in images:
            received = await self._images.read(image)
            if received is None:
                lines.append(IMAGE_UNREADABLE_LINE)
                continue
            outcome: ProofOutcome = await self._proofs.handle(
                conversation, received, message_id=image.message_id, read=read
            )
            recorded = recorded or outcome.kind == "registrado"
            if outcome.context:
                lines.append(outcome.context)
        if not lines:
            return turn, recorded
        return dataclasses.replace(turn, comprobante="; ".join(lines)), recorded

    def _submit_waiting_proof(self, conversation: Conversation) -> bool:
        """Step 13: send a waiting receipt's order to the operator if it is complete."""
        if self._proofs is None:
            return False
        return self._proofs.submit_waiting(conversation) is not None

    def _gate(self, conversation: Conversation, facts: Facts, now: datetime) -> GateReason | None:
        """Step 7: the reasons the assistant stays quiet.

        The paused assistant and the opt-out belong to the real channel: the
        simulator is how a paused business tries its agent (Clarification 002),
        and its synthetic contact never asks to be left alone. A handover applies
        to both, and it applies *even if the stage is VERIFICACION*: a pending
        payment wins in the funnel, not in the gate (`plantillas.md` section 1).
        """
        if not self._sandbox:
            tenant = self._session.get(Tenant, self._tenant_id)
            if tenant is None or not agent_may_reply(self._session, tenant):
                return "paused"
            if conversation.contact_id is not None:
                contact = ContactRepository(self._session, self._tenant_id).get(
                    conversation.contact_id
                )
                if contact is not None and contact.opted_out_at is not None:
                    return "opted_out"
                if contact is not None and not in_pilot(self._session, self._tenant_id, contact.id):
                    return "outside_pilot"
        # A person has the conversation in the inbox (Feature 007): the agent is
        # quiet, in the simulator too, and spends no quota (C-004).
        if conversation.takeover_user_id is not None:
            return "takeover"
        if facts.derivado_activo(now):
            return "handover"
        return None

    def _save(
        self,
        conversation_id: uuid.UUID,
        stage: Stage,
        funnel: FunnelCode,
        patch: Mapping[str, Any] | None,
        version: int,
        *,
        snapshot: Mapping[str, Any] | None = None,
        reason: str = REASON_TURN,
    ) -> int:
        """Save and return the new version; once more on a collision that got away.

        `StateRepo.save` already retries once. A second failure means two writers
        at once (the operator confirming a payment while the model answers): the
        row is re-read and the same intent is applied on top of it.
        """
        for attempt in (1, 2):
            try:
                saved = self._states.save(
                    conversation_id,
                    stage=stage,
                    funnel=funnel,
                    facts_patch=patch,
                    reason=reason,
                    expected_version=version,
                    snapshot=snapshot,
                )
            except StateConflict:
                if attempt == 2:
                    raise
                fresh = self._states.get(conversation_id)
                if fresh is None:  # pragma: no cover - the row was just written
                    raise
                version = fresh.version
                continue
            return saved.state.version
        raise StateConflict("unreachable")  # pragma: no cover


def _same_text(text: str) -> str:
    """Text compared for repetition: case, spacing and WhatsApp markup aside."""
    return " ".join(text.replace("*", "").replace("_", "").split()).casefold()


def _entry_from(referral: Referral, now: datetime) -> dict[str, Any]:
    """The `entrada_anuncio` fact for an ad (`Referral` -> stored facts)."""
    entry = EntradaAnuncio(
        ad_id=referral.source_id,
        source_url=referral.source_url,
        headline=referral.headline,
        body=referral.body,
        recibido_en=now,
    )
    return entry.model_dump(mode="json")
