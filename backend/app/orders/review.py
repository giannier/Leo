"""The operator's decisions on an order (Feature 004, TASK-430, `contracts/api.md` section 5).

Confirm, reject, or move an order along (`en_curso`, `completado`, `descartado`). Each
is one transaction: the order and its latest proof change state, the action is written
to `audit_logs` (C-20) and the conversation's stage is recomputed with its event. The
notice to the customer is *not* part of it: it goes out after the commit
(`services/order_notices.py`), so a message is never sent about a decision that then
rolled back, and a provider that is down never undoes the decision.

The order row is locked first (`SELECT ... FOR UPDATE`). Two operators clicking
Confirm at once queue there; the second finds the order already `confirmado` and gets
`already_reviewed`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from app.models.orders import Order, PaymentProof
from app.orders.service import (
    SALE_MADE,
    TRANSITIONS,
    InvalidTransition,
    OrderError,
    OrderService,
)
from app.process.recompute import REASON_CONFIRM, REASON_REJECT, REASON_STATUS, recompute_stage
from app.repositories.audit import AuditLogRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.orders import (
    OrderRepository,
    OrderSettingsRepository,
    PaymentProofRepository,
)

# The system message that tells the customer (`DEFAULT_SYSTEM_MESSAGES`), if any.
NoticeKind = Literal[
    "pago_confirmado",
    "pago_rechazado",
    "pedido_rechazado",
    "pedido_anulado",
    "pedido_listo",
    "pedido_en_camino",
]

# What `POST .../status` may set: the operator moves a confirmed order along or drops it.
OPERATOR_STATES = ("en_curso", "completado", "descartado")

# The proofs that still stand for a payment; discarding the order voids them.
LIVE_PROOF_STATES = frozenset({"por_verificar", "aprobado"})
# Discarding from these states means the business had accepted the money.
REFUND_STATES = frozenset({"confirmado", "en_curso"})

ACTION_CONFIRMED = "order.confirmed"
ACTION_REJECTED = "order.rejected"
ACTION_STATUS = "order.status_changed"
ACTION_STAGE = "order.stage_changed"
ACTION_REFUND_RESOLVED = "order.refund_resolved"


class OrderNotFound(OrderError):
    """No such order for this tenant (or it belongs to another one)."""


class AlreadyReviewed(OrderError):
    """The payment was already confirmed or rejected."""


class ReasonRequired(OrderError):
    """Discarding an order the customer paid for needs a reason: the customer is told."""


class NoticePreparer(Protocol):
    """Writes the customer's notice in the decision's transaction (finding E-4).

    `CustomerNotifier` implements it; the review service calls it right before
    committing, so the decision and the `queued` notice are one commit.
    """

    def prepare(
        self,
        order: Order,
        kind: NoticeKind,
        *,
        siguiente_paso: str | None = None,
        motivo: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class ReviewResult:
    """What a decision did, for the response and for the customer's notice."""

    order: Order
    notice: NoticeKind | None = None
    proof: PaymentProof | None = None
    # What the notifier prepared (a `PreparedNotice`), to deliver after the commit.
    prepared: Any = None


class OrderReviewService:
    """Confirms, rejects and moves the orders of one tenant."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        now: datetime | None = None,
        notifier: NoticePreparer | None = None,
    ) -> None:
        self._session = session
        self._notifier = notifier
        self._tenant_id = tenant_id
        self._fixed_now = now
        self._orders = OrderRepository(session, tenant_id)
        self._proofs = PaymentProofRepository(session, tenant_id)
        self._service = OrderService(session, tenant_id, now=now)

    @property
    def now(self) -> datetime:
        return self._fixed_now or datetime.now(UTC)

    # --------------------------------------------------------------- confirm

    def confirm(
        self, order_id: uuid.UUID, user_id: uuid.UUID, *, siguiente_paso: str | None = None
    ) -> ReviewResult:
        """The business accepts the payment (or the cash-on-delivery order)."""
        order = self._waiting(order_id, "confirmado")
        proof = self._latest_open_proof(order)
        if proof is not None:
            proof.estado = "aprobado"
            proof.reviewed_by = user_id
            proof.reviewed_at = self.now
        order.siguiente_paso = (siguiente_paso or "").strip() or None
        self._service.transition(order, "confirmado", user_id=user_id)
        self._audit(user_id, ACTION_CONFIRMED, order, "por_confirmar", "confirmado", proof)
        self._restage(order, REASON_CONFIRM)
        prepared = self._prepare(order, "pago_confirmado", siguiente_paso=order.siguiente_paso)
        self._session.commit()
        return ReviewResult(order, "pago_confirmado", proof, prepared)

    # ---------------------------------------------------------------- reject

    def reject(self, order_id: uuid.UUID, user_id: uuid.UUID, *, motivo: str) -> ReviewResult:
        """The business sends the order back with a reason.

        With a receipt: the receipt is rejected and the payment is `rechazado` until
        the customer changes something or sends another (`rechazo_vigente`, C-21).
        Without one (cash on delivery): the order returns to checkout with the reason
        and no rejection in force.
        """
        order = self._waiting(order_id, "borrador")
        proof = self._latest_open_proof(order)
        if proof is not None:
            proof.estado = "rechazado"
            proof.motivo_rechazo = motivo
            proof.reviewed_by = user_id
            proof.reviewed_at = self.now
        self._service.transition(
            order, "borrador", motivo=motivo, rechazo_vigente=proof is not None
        )
        self._audit(user_id, ACTION_REJECTED, order, "por_confirmar", "borrador", proof, motivo)
        # A receipt rejection leaves the stage in VERIFICACION (waiting -> rejected), so
        # without `force_event` the decision would leave no event at all.
        self._restage(order, REASON_REJECT, force_event=True)
        kind: NoticeKind = "pago_rechazado" if proof is not None else "pedido_rechazado"
        prepared = self._prepare(order, kind, motivo=motivo)
        self._session.commit()
        return ReviewResult(order, kind, proof, prepared)

    # ---------------------------------------------------------------- status

    def set_status(
        self,
        order_id: uuid.UUID,
        user_id: uuid.UUID,
        estado: str,
        *,
        motivo: str | None = None,
    ) -> ReviewResult:
        """Move a confirmed order along, or discard it.

        Moving along tells the customer nothing. Discarding an order with a live
        payment proof (waiting or approved) is different: the customer paid, so it
        takes a reason, the proofs are voided (`anulado`) and the customer is told.
        If the business had already confirmed it, a refund is owed and the order is
        marked `devolucion_pendiente` for the operator (Milestone B, E-2).
        """
        order = self._lock(order_id)
        before = order.estado
        if estado not in TRANSITIONS[before]:
            # A move the matrix forbids is a conflict, whether or not it has a reason.
            raise InvalidTransition(before, estado)
        live = (
            [p for p in self._proofs.for_order(order.id) if p.estado in LIVE_PROOF_STATES]
            if estado == "descartado"
            else []
        )
        reason = (motivo or "").strip() or None
        if live and reason is None:
            raise ReasonRequired("discarding an order with a payment proof needs a reason")
        self._service.transition(order, estado)
        for proof in live:
            proof.estado = "anulado"
            proof.motivo_rechazo = reason
            proof.reviewed_by = user_id
            proof.reviewed_at = self.now
        if live and before in REFUND_STATES:
            order.devolucion_pendiente = True
        self._audit(
            user_id, ACTION_STATUS, order, before, estado, live[0] if live else None, reason
        )
        self._restage(order, REASON_STATUS)
        prepared = self._prepare(order, "pedido_anulado", motivo=reason) if live else None
        self._session.commit()
        return ReviewResult(
            order, "pedido_anulado" if live else None, live[0] if live else None, prepared
        )

    # ----------------------------------------------------------------- stage

    def set_stage(self, order_id: uuid.UUID, user_id: uuid.UUID, etapa: str) -> ReviewResult:
        """Move the business's work on a confirmed order (feature 016).

        Starting the work moves the order to `en_curso`, so the conversation's stage is
        recomputed; moving between stages is the business's own business. The customer is told
        when a pickup is ready or a delivery is on its way, if the business wants that.
        """
        order = self._lock(order_id)
        before, stage_before = order.estado, order.etapa
        self._service.set_stage(order, etapa)
        self._audit_stage(user_id, order, before, stage_before)
        if order.estado != before:
            self._restage(order, REASON_STATUS)
        kind = self._stage_notice(order)
        prepared = self._prepare(order, kind) if kind is not None else None
        self._session.commit()
        return ReviewResult(order, kind, None, prepared)

    def resolve_refund(self, order_id: uuid.UUID, user_id: uuid.UUID) -> Order:
        """The business paid the money back: the order stops being owed (Milestone B, E-2)."""
        order = self._lock(order_id)
        if not order.devolucion_pendiente:
            raise OrderError("this order owes no refund")
        order.devolucion_pendiente = False
        AuditLogRepository(self._session, self._tenant_id).record(
            actor_id=user_id,
            action=ACTION_REFUND_RESOLVED,
            detail={"order_id": str(order.id), "numero": order.numero},
        )
        self._session.commit()
        return order

    def _stage_notice(self, order: Order) -> NoticeKind | None:
        """The notice a stage sends, if this order's way of delivery and the settings want it."""
        settings = OrderSettingsRepository(self._session, self._tenant_id).ensure()
        if order.etapa == "listo" and order.tipo_entrega == "recojo" and settings.avisar_listo:
            return "pedido_listo"
        if (
            order.etapa == "en_camino"
            and order.tipo_entrega == "delivery"
            and settings.avisar_en_camino
        ):
            return "pedido_en_camino"
        return None

    def _audit_stage(
        self, user_id: uuid.UUID, order: Order, before: str, stage_before: str | None
    ) -> None:
        detail: dict[str, object] = {
            "order_id": str(order.id),
            "numero": order.numero,
            "de": before,
            "a": order.estado,
            "etapa_de": stage_before,
            "etapa_a": order.etapa,
        }
        AuditLogRepository(self._session, self._tenant_id).record(
            actor_id=user_id, action=ACTION_STAGE, detail=detail
        )

    # --------------------------------------------------------------- helpers

    def _prepare(
        self,
        order: Order,
        kind: NoticeKind,
        *,
        siguiente_paso: str | None = None,
        motivo: str | None = None,
    ) -> Any:
        if self._notifier is None:
            return None
        self._session.flush()
        return self._notifier.prepare(order, kind, siguiente_paso=siguiente_paso, motivo=motivo)

    def _lock(self, order_id: uuid.UUID) -> Order:
        order = self._orders.get_for_update(order_id)
        if order is None:
            raise OrderNotFound(str(order_id))
        return order

    def _waiting(self, order_id: uuid.UUID, target: str) -> Order:
        """The order, locked, if it is waiting for the business.

        One that was already confirmed is `AlreadyReviewed` (a double click); any other
        state cannot make this move and is `InvalidTransition`.
        """
        order = self._lock(order_id)
        if order.estado in SALE_MADE:
            raise AlreadyReviewed("the order was already confirmed")
        if order.estado != "por_confirmar":
            raise InvalidTransition(order.estado, target)
        return order

    def _latest_open_proof(self, order: Order) -> PaymentProof | None:
        """The receipt the operator is looking at: the newest one, if still open."""
        proof = self._proofs.latest_for_order(order.id)
        return proof if proof is not None and proof.estado == "por_verificar" else None

    def _restage(self, order: Order, reason: str, *, force_event: bool = False) -> None:
        conversation = ConversationRepository(self._session, self._tenant_id).get(
            order.conversation_id
        )
        if conversation is not None:
            self._session.flush()
            recompute_stage(
                self._session,
                self._tenant_id,
                conversation,
                reason=reason,
                force_event=force_event,
            )

    def _audit(
        self,
        user_id: uuid.UUID,
        action: str,
        order: Order,
        before: str,
        after: str,
        proof: PaymentProof | None,
        motivo: str | None = None,
    ) -> None:
        detail: dict[str, object] = {
            "order_id": str(order.id),
            "numero": order.numero,
            "de": before,
            "a": after,
        }
        if proof is not None:
            detail["proof_id"] = str(proof.id)
        if motivo:
            detail["motivo"] = motivo
        AuditLogRepository(self._session, self._tenant_id).record(
            actor_id=user_id, action=action, detail=detail
        )
