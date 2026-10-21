"""Leads that move with the sales process, and that a person can always correct (Feature 012).

`LeadService.follow` is called by `StateRepo.save` every time a conversation's stage is written,
so the lead moves deterministically with the process (C-102): no tool for the model, no prompt
change, no LLM cost. People move, assign, annotate and discard through the API, and a person's
move wins (C-103): afterwards the process only advances the lead with evidence (an order or a
confirmed order), never backwards.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.conversation_state import ConversationState
from app.models.crm import (
    LOST_NO_REPLY,
    MAX_STAGES,
    SEMANTIC_STAGES,
    Lead,
    LeadEvent,
    PipelineStage,
)
from app.models.orders import Order
from app.process.facts import CONFIRMED_ORDER_STATES
from app.repositories.crm import (
    STAGE_STEP,
    LeadEventRepository,
    LeadRepository,
    LostReasonRepository,
    PipelineStageRepository,
)
from app.repositories.orders import OrderItemRepository, OrderRepository

# The stages a sale moves through by itself, in order. `perdido` is not a step forward.
FORWARD = ("nuevo", "interesado", "por_pagar", "ganado")
EVIDENCE = frozenset({"por_pagar", "ganado"})
# Why the process discarded a stale draft (followups.REASON_DISCARDED).
REASON_DISCARDED = "pedido_descartado"
# Causes written by the model's turn; everything else the process does is the system.
_AGENT_CAUSES = ("turno", "herramienta:")


class CrmError(ValueError):
    """A request the CRM refuses, with a code for the API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def free_key(label: str, taken: set[str], *, fallback: str) -> str:
    """ "Cotización enviada" -> "cotizacion_enviada", with a number if it is taken."""
    plain = unicodedata.normalize("NFKD", label.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:30] or fallback
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def semantic_for(stage_key: str, facts: dict[str, Any], order: Order | None) -> str | None:
    """Where the process puts the conversation on the board (C-102), or None for no lead."""
    if order is not None and order.estado in CONFIRMED_ORDER_STATES:
        return "ganado"
    if stage_key == "CERRADO":
        return "ganado" if order is not None else None
    if stage_key in ("COBRO", "VERIFICACION"):
        return "por_pagar"
    if stage_key in ("PEDIDO", "DATOS", "ASESORIA"):
        return "interesado"
    if stage_key == "DERIVADO" and (
        facts.get("asesoria_derivada_en")
        or (facts.get("derivado") or {}).get("motivo") in ("asesoria", "cotizacion")
    ):
        # Handed to an advisor to quote or advise: a qualified lead, not a complaint.
        return "interesado"
    if facts.get("cita_agendada"):
        # An appointment is evidence of interest whatever stage the conversation is in.
        return "interesado"
    if stage_key == "IDENTIFICAR" and facts.get("vio_productos"):
        return "nuevo"
    return None


def actor_for(cause: str) -> str:
    return "agent" if cause.startswith(_AGENT_CAUSES) else "system"


@dataclass(frozen=True)
class Board:
    stages: list[PipelineStage]

    def rank(self, stage: PipelineStage) -> float:
        """A stage's place in the forward order.

        A business's own stage sits just after the fixed stage before it.
        """
        if stage.semantic in FORWARD:
            return float(FORWARD.index(stage.semantic))
        if stage.semantic == "perdido":
            return float(len(FORWARD))
        before = [s for s in self.stages if s.position < stage.position and s.semantic in FORWARD]
        return (FORWARD.index(before[-1].semantic) + 0.5) if before else -0.5

    def semantic(self, key: str) -> PipelineStage:
        found = next((s for s in self.stages if s.semantic == key), None)
        if found is None:  # pragma: no cover - ensure_defaults creates all five
            raise LookupError(key)
        return found


class LeadService:
    def __init__(self, session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None):
        self._session = session
        self._tenant_id = tenant_id
        self._now = now
        self.stages = PipelineStageRepository(session, tenant_id)
        self.leads = LeadRepository(session, tenant_id)
        self.events = LeadEventRepository(session, tenant_id)
        self.reasons = LostReasonRepository(session, tenant_id)

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    def board(self) -> Board:
        self.stages.ensure_defaults()
        return Board(self.stages.ordered())

    # ------------------------------------------------------------ the process

    def follow(self, state: ConversationState, cause: str) -> Lead | None:
        """Move the contact's lead after the conversation's stage was written."""
        if state.is_sandbox:
            return None
        conversation = self._session.get(Conversation, state.conversation_id)
        if (
            conversation is None
            or conversation.tenant_id != self._tenant_id
            or conversation.contact_id is None
        ):
            return None
        board = self.board()
        lead = self.leads.active_for_contact(conversation.contact_id)
        moment = self._moment()

        if cause == REASON_DISCARDED:
            # The draft expired without an answer: lost, unless a person is working it.
            if lead is not None and not lead.moved_by_person:
                self._close(
                    lead, board.semantic("perdido"), "lost", cause, "system", None, LOST_NO_REPLY
                )
            return lead

        order = OrderRepository(self._session, self._tenant_id).active_for_conversation(
            conversation.id
        )
        facts = dict(state.facts or {})
        target = semantic_for(state.stage_key, facts, order)

        if lead is None:
            if target is None:
                return None
            latest = self.leads.latest_for_contact(conversation.contact_id)
            if latest is not None:
                if order is not None and latest.order_id == order.id and latest.status != "active":
                    return None  # this sale was already counted (won, lost or discarded)
                if latest.status == "discarded" and target not in EVIDENCE:
                    return None  # "no es lead" holds until there is an order
            lead = self.leads.add(
                contact_id=conversation.contact_id,
                conversation_id=conversation.id,
                stage_id=board.semantic(target).id,
                previous_lead_id=latest.id if latest is not None else None,
                origen=self._origin(state),
                stage_entered_at=moment,
                last_activity_at=moment,
            )
            self._session.flush()
            self._record(lead, None, board.semantic(target), cause, actor_for(cause), None)
            self._amounts(lead, order)
            self._quote(lead, facts)
            if target == "ganado":
                self._close(lead, board.semantic("ganado"), "won", cause, actor_for(cause), None)
            return lead

        lead.conversation_id = conversation.id
        lead.last_activity_at = moment
        self._amounts(lead, order)
        self._quote(lead, facts)
        if target is None:
            return lead
        current = self.stages.get(lead.stage_id)
        goal = board.semantic(target)
        if current is None or board.rank(goal) <= board.rank(current):
            return lead  # never backwards
        if lead.moved_by_person and target not in EVIDENCE:
            return lead  # a person moved it; only an order moves it now
        if target == "ganado":
            self._close(lead, goal, "won", cause, actor_for(cause), None)
        else:
            self._move(lead, current, goal, cause, actor_for(cause), None)
        return lead

    def _quote(self, lead: Lead, facts: dict[str, Any]) -> None:
        """The quote the agent handed over (`derivado` with motivo cotizacion) becomes the lead's.

        A newer hand-over starts a new request: a customer who asks again after one was sent
        is waiting for a new quote.
        """
        handed = facts.get("derivado")
        if not isinstance(handed, dict) or handed.get("motivo") != "cotizacion":
            return
        try:
            since = datetime.fromisoformat(str(handed.get("desde")))
        except ValueError:
            return
        if lead.quote_requested_at is not None and since <= lead.quote_requested_at:
            return
        data = handed.get("datos")
        lead.quote_status = "solicitada"
        lead.quote_summary = handed.get("resumen") or None
        lead.quote_data = dict(data) if isinstance(data, dict) else {}
        lead.quote_requested_at = since
        lead.quote_sent_at = None
        self._event(
            lead, "turno:cotizacion_solicitada", "agent", None, detail=dict(lead.quote_data)
        )

    def quote_sent(
        self,
        lead: Lead,
        *,
        by: uuid.UUID,
        amount: Decimal | None = None,
        moneda: str | None = None,
    ) -> Lead:
        """A person sent the quote: the amount (if they say it) is the lead's estimated amount."""
        if lead.status != "active" or lead.quote_status is None:
            raise CrmError("no_quote", "Este lead no tiene una cotización pedida.")
        now = self._moment()
        lead.quote_status = "enviada"
        lead.quote_sent_at = now
        lead.last_activity_at = now
        if amount is not None:
            lead.amount_estimated = amount
            lead.moneda = moneda or lead.moneda
        self._event(
            lead,
            "persona:cotizacion_enviada",
            "user",
            by,
            detail={"monto": str(amount) if amount is not None else ""},
        )
        return lead

    def _origin(self, state: ConversationState) -> str | None:
        entry = (state.facts or {}).get("entrada_anuncio")
        if isinstance(entry, dict):
            shown = entry.get("headline") or entry.get("body") or entry.get("source_url")
            return f"Anuncio: {str(shown)[:120]}" if shown else "Anuncio"
        return None

    def _amounts(self, lead: Lead, order: Order | None) -> None:
        if order is None:
            return
        lead.order_id = order.id
        lead.moneda = order.moneda
        total = Decimal(order.total)
        if order.estado in CONFIRMED_ORDER_STATES:
            lead.amount_real = total
        elif total > 0:
            lead.amount_estimated = total
        items = OrderItemRepository(self._session, self._tenant_id).for_order(order.id)
        if items:
            lead.product_interest = items[0].nombre[:200]

    # ---------------------------------------------------------------- people

    def move(
        self, lead: Lead, stage_id: uuid.UUID, *, by: uuid.UUID, lost_reason: str | None = None
    ) -> Lead:
        board = self.board()
        target = next((s for s in board.stages if s.id == stage_id and s.is_active), None)
        if target is None:
            raise CrmError("unknown_stage", "Esa etapa no existe o está desactivada.")
        if lead.status != "active" and target.semantic not in ("ganado", "perdido"):
            # Reopening a closed lead: it becomes the contact's active lead again.
            if self.leads.active_for_contact(lead.contact_id) is not None:
                raise CrmError("another_active", "Ese contacto ya tiene otro lead activo; usa ese.")
            lead.status = "active"
            lead.closed_at = None
            lead.lost_reason = None
        current = self.stages.get(lead.stage_id)
        lead.moved_by_person = True
        lead.last_activity_at = self._moment()
        if target.semantic == "perdido":
            reason = lost_reason or "otro"
            if self.reasons.by_key(reason) is None:
                raise CrmError("unknown_reason", "Elige un motivo de pérdida de la lista.")
            self._close(lead, target, "lost", "persona:mover", "user", by, reason)
        elif target.semantic == "ganado":
            self._close(lead, target, "won", "persona:mover", "user", by)
        else:
            self._move(lead, current, target, "persona:mover", "user", by)
        return lead

    def assign(self, lead: Lead, user_id: uuid.UUID | None, *, by: uuid.UUID) -> Lead:
        lead.assigned_to = user_id
        self._event(lead, "persona:asignar", "user", by, detail={"asignado": str(user_id or "")})
        return lead

    def discard(self, lead: Lead, *, by: uuid.UUID) -> Lead:
        """ "No es lead": out of the board, and not counted as lost (C-002)."""
        lead.status = "discarded"
        lead.closed_at = self._moment()
        self._event(lead, "persona:descartar", "user", by)
        return lead

    # ------------------------------------------------------------- the board

    def save_stages(self, items: list[dict[str, Any]]) -> list[PipelineStage]:
        """Rename and switch the business's stages; add, place or remove its own (max 8)."""
        board = self.board()
        by_key = {s.key: s for s in board.stages}
        keys_in = [item["key"] for item in items if item.get("key")]
        if len(set(keys_in)) != len(keys_in):
            raise CrmError("duplicate_stage", "Hay etapas repetidas.")
        if len(items) > MAX_STAGES:
            raise CrmError("too_many_stages", f"Hasta {MAX_STAGES} etapas.")
        if [item.get("key") for item in items if item.get("key") in SEMANTIC_STAGES] != [
            key for key in SEMANTIC_STAGES if key in keys_in
        ] or not set(SEMANTIC_STAGES) <= set(keys_in):
            raise CrmError(
                "fixed_stages",
                "Las cinco etapas fijas van todas y en su orden; renómbralas si quieres.",
            )
        kept: set[str] = set()
        for position, item in enumerate(items, start=1):
            key = item.get("key") or self._new_key(item["label"], by_key)
            stage = by_key.get(key)
            active = bool(item.get("is_active", True))
            if stage is None:
                stage = self.stages.add(key=key, label=item["label"], position=0, semantic=None)
                by_key[key] = stage
            if stage.semantic in ("nuevo", "ganado", "perdido") and not active:
                raise CrmError("fixed_stages", "Nuevo, Ganado y Perdido no se pueden desactivar.")
            stage.label = item["label"]
            stage.position = position * STAGE_STEP
            stage.is_active = active
            kept.add(key)
        for key, stage in by_key.items():
            if key in kept or stage.semantic is not None:
                continue
            if self.leads.count(Lead.stage_id == stage.id, Lead.status == "active"):
                raise CrmError(
                    "stage_in_use", f'Mueve primero los leads de "{stage.label}" a otra etapa.'
                )
            self._session.delete(stage)
        self._session.flush()
        return self.stages.ordered()

    @staticmethod
    def _new_key(label: str, taken: dict[str, PipelineStage]) -> str:
        return free_key(label, set(taken) | set(SEMANTIC_STAGES), fallback="etapa")

    # --------------------------------------------------------------- helpers

    def _move(
        self,
        lead: Lead,
        current: PipelineStage | None,
        target: PipelineStage,
        cause: str,
        actor: str,
        by: uuid.UUID | None,
    ) -> None:
        if current is not None and current.id == target.id:
            return
        lead.stage_id = target.id
        lead.stage_entered_at = self._moment()
        self._record(lead, current, target, cause, actor, by)

    def _close(
        self,
        lead: Lead,
        target: PipelineStage,
        status: str,
        cause: str,
        actor: str,
        by: uuid.UUID | None,
        reason: str | None = None,
    ) -> None:
        current = self.stages.get(lead.stage_id)
        lead.status = status
        lead.closed_at = self._moment()
        lead.lost_reason = reason if status == "lost" else None
        if current is None or current.id != target.id:
            lead.stage_id = target.id
            lead.stage_entered_at = self._moment()
        self._record(
            lead, current, target, cause, actor, by, detail={"motivo": reason} if reason else None
        )

    def _record(
        self,
        lead: Lead,
        current: PipelineStage | None,
        target: PipelineStage,
        cause: str,
        actor: str,
        by: uuid.UUID | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.events.add(
            lead_id=lead.id,
            actor=actor,
            actor_user_id=by,
            from_stage=current.key if current is not None else None,
            to_stage=target.key,
            cause=cause,
            detail=detail or {},
        )

    def _event(
        self,
        lead: Lead,
        cause: str,
        actor: str,
        by: uuid.UUID | None,
        detail: dict[str, Any] | None = None,
    ) -> LeadEvent:
        # The wall clock, not the transaction's now(): several moves in one turn keep their order.
        return self.events.add(
            lead_id=lead.id,
            actor=actor,
            actor_user_id=by,
            cause=cause,
            detail=detail or {},
            created_at=datetime.now(UTC),
        )
