"""Automatic follow-ups and stale drafts (Feature 004, TASK-444, C-13).

**Follow-ups.** When the customer goes quiet, the assistant writes once more, with
the stage's follow-up instruction (`plantillas.md` section 6): one bubble, no tools.
A follow-up is sent only if *all* of these hold (`why_not` names the first one that
does not):

* the business turned follow-ups on (`agent_configs.seguimiento_activo`);
* the conversation is on the real channel and the stage is IDENTIFICAR, PEDIDO,
  DATOS or COBRO, or VERIFICACION with the payment rejected;
* nobody took it (`derivado`), the customer did not opt out, the assistant may reply;
* the last message is ours and is at least `seguimiento_horas` old;
* attempts left (`seguimiento_max`);
* Meta's 24-hour window stays open for 30 more minutes;
* it is 08:00 to 21:00 where the business is.

The customer writing again resets the count (the turn pipeline clears `seguimiento`).

**No double send.** The worker holds the conversation's lock (the same one a turn
takes), and the attempt is claimed by a versioned write of `seguimiento` before the
model is called: a second worker that read the same state loses the write and
stops. A claimed attempt whose message then fails is spent, not retried: one
missing reminder is better than two.

**The last attempt** may carry a promotion marked `para_seguimiento` (a closing
promotion, with its own hours). It is recorded in `promos_ofrecidas` with its
expiry before the message goes, so the customer can then use it.

**Stale drafts** (`orders.discard_stale`, daily): a draft with no activity for 7
days becomes `descartado` and the stage is recomputed (event `pedido_descartado`).
A draft with a payment receipt is left for a person.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.agent.core import AgentCore, usable_answer
from app.agent.prompts.stage import build_stage_prompt
from app.models.business import AgentConfig, BusinessProfile
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationState, FunnelCode
from app.models.orders import Order, PaymentProof
from app.models.sales_config import Promotion
from app.models.tenant import Tenant
from app.orders.service import OrderService
from app.process.facts import Facts, FactsLoader
from app.process.recompute import recompute_stage
from app.process.service import EffectiveProcessConfig, ProcessConfigService
from app.process.state import StateConflict, StateRepo
from app.process.templates.base import Stage
from app.providers.llm.base import ChatMessage, LLMError, LLMProvider
from app.repositories.channel import ContactRepository
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.services.trial import agent_may_reply, record_ai_message

logger = logging.getLogger(__name__)

FOLLOW_UP_STAGES = frozenset({Stage.IDENTIFICAR, Stage.PEDIDO, Stage.DATOS, Stage.COBRO})
WINDOW = timedelta(hours=24)
WINDOW_MARGIN = timedelta(minutes=30)
SENDING_HOURS = (time(8, 0), time(21, 0))
DEFAULT_ZONE = "America/Lima"
STALE_DRAFT = timedelta(days=7)
REASON_FOLLOW_UP = "seguimiento"
REASON_DISCARDED = "pedido_descartado"
_BATCH = 500

# The reasons `why_not` gives, for the logs and the tests.
Skip = str


@dataclass(frozen=True)
class FollowUpPlan:
    """A follow-up that is due: which attempt, and whether it is the last."""

    attempt: int
    last: bool


def local_time(now: datetime, zone: str | None) -> time:
    try:
        tz = ZoneInfo(zone or DEFAULT_ZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo(DEFAULT_ZONE)
    return now.astimezone(tz).time()


def why_not(
    *,
    now: datetime,
    stage: Stage,
    facts: Facts,
    config: AgentConfig | None,
    sandbox: bool,
    may_reply: bool,
    contact: Contact | None,
    last_message: Message | None,
    zone: str | None,
) -> Skip | FollowUpPlan:
    """The first condition of C-13 that fails, or the follow-up that is due."""
    if config is None or not config.seguimiento_activo:
        return "apagado"
    if sandbox:
        return "simulador"
    if stage is Stage.VERIFICACION:
        if facts.pago_estado != "rechazado":
            return "etapa"
    elif stage not in FOLLOW_UP_STAGES:
        return "etapa"
    if facts.derivado_activo(now):
        return "derivado"
    if not may_reply:
        return "asistente_inactivo"
    if contact is None or contact.opted_out_at is not None:
        return "opt_out"
    if last_message is None or last_message.direction != "outbound":
        return "sin_respuesta_nuestra"
    quiet_since = last_message.created_at
    if facts.seguimiento is not None and facts.seguimiento.ultimo_en is not None:
        quiet_since = max(quiet_since, facts.seguimiento.ultimo_en)
    if now - quiet_since < timedelta(hours=config.seguimiento_horas):
        return "espera"
    done = facts.seguimiento.intentos if facts.seguimiento is not None else 0
    if done >= config.seguimiento_max:
        return "intentos"
    if contact.last_inbound_at is None or now + WINDOW_MARGIN >= contact.last_inbound_at + WINDOW:
        return "ventana"
    start, end = SENDING_HOURS
    if not start <= local_time(now, zone) < end:
        return "horario"
    return FollowUpPlan(attempt=done + 1, last=done + 1 >= config.seguimiento_max)


def due_candidates(session: Session, *, limit: int = _BATCH) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant, conversation) of real-channel conversations in a follow-up stage.

    Platform-wide, like `stranded_replies`: each one is re-read scoped to its
    tenant and checked by `why_not` before anything is written.
    """
    stages = [stage.value for stage in (*FOLLOW_UP_STAGES, Stage.VERIFICACION)]
    statement = (
        select(ConversationState.tenant_id, ConversationState.conversation_id)
        .where(ConversationState.is_sandbox.is_(False), ConversationState.stage_key.in_(stages))
        .order_by(ConversationState.updated_at)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(statement).all()]


@dataclass(frozen=True)
class FollowUpResult:
    outcome: str
    message: Message | None = None


class FollowUps:
    """Decides, claims and writes one conversation's follow-up."""

    def __init__(self, session: Session, tenant_id: uuid.UUID, *, llm: LLMProvider) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm

    async def run(
        self, conversation_id: uuid.UUID, *, now: datetime | None = None
    ) -> FollowUpResult:
        """Write the follow-up as a `queued` message, or say why not. Commits nothing."""
        moment = now or datetime.now(UTC)
        session, tenant_id = self._session, self._tenant_id
        conversation = ConversationRepository(session, tenant_id).get(conversation_id)
        tenant = session.get(Tenant, tenant_id)
        if conversation is None or tenant is None:
            return FollowUpResult("no_existe")
        process = ProcessConfigService(session).effective(tenant_id)
        states = StateRepo(session, tenant_id)
        state = states.get_or_create(conversation, process.template)
        facts = FactsLoader(session, tenant_id, process.facts_config()).load(
            conversation, now=moment
        )
        stage = Stage(state.stage_key)
        config = session.execute(
            select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)
        ).scalar_one_or_none()
        profile = session.execute(
            select(BusinessProfile).where(BusinessProfile.tenant_id == tenant_id)
        ).scalar_one_or_none()
        contact = (
            ContactRepository(session, tenant_id).get(conversation.contact_id)
            if conversation.contact_id is not None
            else None
        )
        plan = why_not(
            now=moment,
            stage=stage,
            facts=facts,
            config=config,
            sandbox=conversation.channel == "sandbox",
            may_reply=agent_may_reply(session, tenant),
            contact=contact,
            last_message=MessageRepository(session, tenant_id).latest_for_conversation(
                conversation_id
            ),
            zone=profile.timezone if profile is not None else None,
        )
        if not isinstance(plan, FollowUpPlan):
            return FollowUpResult(plan)

        promotion = self._promotion(moment) if plan.last else None
        patch: dict[str, object] = {
            "seguimiento": {"intentos": plan.attempt, "ultimo_en": moment.isoformat()}
        }
        if promotion is not None and promotion.vigencia_horas:
            offers = {key: value.isoformat() for key, value in facts.promos_ofrecidas.items()}
            offers[str(promotion.id)] = (
                moment + timedelta(hours=promotion.vigencia_horas)
            ).isoformat()
            patch["promos_ofrecidas"] = offers
        try:
            states.save(
                conversation_id,
                stage=stage,
                funnel=FunnelCode(state.funnel_code),
                facts_patch=patch,
                reason=REASON_FOLLOW_UP,
                expected_version=state.version,
                retry=False,
            )
        except StateConflict:
            return FollowUpResult("ocupado")

        text = await self._write(conversation, process, facts, stage, plan, promotion, moment)
        if text is None:
            return FollowUpResult("sin_texto")
        message = MessageRepository(session, tenant_id).append(
            conversation_id=conversation_id,
            direction="outbound",
            author="agent",
            content=text,
            delivery_status="queued",
            payload={"seguimiento": plan.attempt},
        )
        record_ai_message(session, tenant_id, channel="whatsapp")
        return FollowUpResult("enviado", message)

    def _promotion(self, now: datetime) -> Promotion | None:
        statement = (
            select(Promotion)
            .where(
                Promotion.tenant_id == self._tenant_id,
                Promotion.is_active.is_(True),
                Promotion.para_seguimiento.is_(True),
                Promotion.uso == "cierre",
            )
            .order_by(Promotion.created_at, Promotion.id)
        )
        for promotion in self._session.execute(statement).scalars():
            if promotion.inicia_en is not None and promotion.inicia_en > now:
                continue
            if promotion.termina_en is not None and promotion.termina_en <= now:
                continue
            return promotion
        return None

    async def _write(
        self,
        conversation: Conversation,
        process: EffectiveProcessConfig,
        facts: Facts,
        stage: Stage,
        plan: FollowUpPlan,
        promotion: Promotion | None,
        now: datetime,
    ) -> str | None:
        texts = process.template.stage(stage).seguimiento
        if texts is None:
            return None
        instruction = texts.ultimo_intento if plan.last else texts.primer_intento
        prompt = build_stage_prompt(process, facts, stage, now=now, tool_specs=())
        lines = [
            "SEGUIMIENTO",
            "El cliente no respondió a tu último mensaje. Escríbele UN solo mensaje breve: "
            f"{instruction}",
            "No repitas tu mensaje anterior, no uses herramientas y no inventes datos.",
        ]
        if promotion is not None:
            detail = f" ({promotion.descripcion})" if promotion.descripcion else ""
            lines.append(
                f"Promoción para seguimiento: «{promotion.nombre}»{detail}, válida "
                f"{promotion.vigencia_horas} horas desde ahora."
            )
        else:
            lines.append("No hay promoción para seguimiento: no ofrezcas descuentos.")
        core = AgentCore(self._session, self._tenant_id, llm=self._llm)
        messages = [
            ChatMessage("system", f"{prompt.text}\n\n" + "\n".join(lines)),
            *core.history(conversation.id),
            ChatMessage("system", "Escribe ahora el mensaje de seguimiento."),
        ]
        try:
            result = await self._llm.chat(messages, tools=())
        except LLMError:
            logger.exception("follow-up failed", extra={"tenant_id": str(self._tenant_id)})
            return None
        content, unusable = usable_answer(result.content)
        return None if unusable else content


# ---------------------------------------------------------------- stale drafts


def stale_drafts(session: Session, *, now: datetime, limit: int = _BATCH) -> Sequence[Order]:
    """Drafts with no activity for 7 days and no receipt. Platform-wide."""
    has_proof = exists().where(PaymentProof.order_id == Order.id)
    statement = (
        select(Order)
        .where(
            Order.estado == "borrador",
            Order.last_activity_at < now - STALE_DRAFT,
            ~has_proof,
        )
        .order_by(Order.last_activity_at)
        .limit(limit)
    )
    return session.execute(statement).scalars().all()


def discard_stale_drafts(session: Session, *, now: datetime | None = None) -> int:
    """Discard them, recompute each conversation's stage; one commit per order."""
    moment = now or datetime.now(UTC)
    done = 0
    for order in stale_drafts(session, now=moment):
        tenant_id = order.tenant_id
        try:
            OrderService(session, tenant_id, now=moment).transition(order, "descartado")
            conversation = ConversationRepository(session, tenant_id).get(order.conversation_id)
            if conversation is not None:
                recompute_stage(
                    session,
                    tenant_id,
                    conversation,
                    reason=REASON_DISCARDED,
                    now=moment,
                    force_event=True,
                )
            session.commit()
            done += 1
        except Exception:  # noqa: BLE001 - one order never stops the rest
            session.rollback()
            logger.exception("stale draft discard failed", extra={"tenant_id": str(tenant_id)})
    return done
