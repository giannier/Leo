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
from typing import Literal

from sqlalchemy.orm import Session

from app.models.orders import Order, PaymentProof
from app.orders.service import SALE_MADE, InvalidTransition, OrderError, OrderService
from app.process.recompute import REASON_CONFIRM, REASON_REJECT, REASON_STATUS, recompute_stage
from app.repositories.audit import AuditLogRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.orders import OrderRepository, PaymentProofRepository

# The system message that tells the customer (`DEFAULT_SYSTEM_MESSAGES`), if any.
NoticeKind = Literal["pago_confirmado", "pago_rechazado", "pedido_rechazado"]

# What `POST .../status` may set: the operator moves a confirmed order along or drops it.
OPERATOR_STATES = ("en_curso", "completado", "descartado")

ACTION_CONFIRMED = "order.confirmed"
ACTION_REJECTED = "order.rejected"
ACTION_STATUS = "order.status_changed"


class OrderNotFound(OrderError):
    """No such order for this tenant (or it belongs to another one)."""


class AlreadyReviewed(OrderError):
    """The payment was already confirmed or rejected."""


@dataclass(frozen=True)
class ReviewResult:
    """What a decision did, for the response and for the customer's notice."""

    order: Order
    notice: NoticeKind | None = None
    proof: PaymentProof | None = None


class OrderReviewService:
    """Confirms, rejects and moves the orders of one tenant."""

    def __init__(
        self, session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        self._session = session
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
        self._session.commit()
        return ReviewResult(order, "pago_confirmado", proof)

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
        self._restage(order, REASON_REJECT)
        self._session.commit()
        return ReviewResult(
            order, "pago_rechazado" if proof is not None else "pedido_rechazado", proof
        )

    # ---------------------------------------------------------------- status

    def set_status(self, order_id: uuid.UUID, user_id: uuid.UUID, estado: str) -> ReviewResult:
        """Move a confirmed order along, or discard it. No notice to the customer."""
        order = self._lock(order_id)
        before = order.estado
        self._service.transition(order, estado)
        self._audit(user_id, ACTION_STATUS, order, before, estado, None)
        self._restage(order, REASON_STATUS)
        self._session.commit()
        return ReviewResult(order)

    # --------------------------------------------------------------- helpers

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

    def _restage(self, order: Order, reason: str) -> None:
        conversation = ConversationRepository(self._session, self._tenant_id).get(
            order.conversation_id
        )
        if conversation is not None:
            self._session.flush()
            recompute_stage(self._session, self._tenant_id, conversation, reason=reason)

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
