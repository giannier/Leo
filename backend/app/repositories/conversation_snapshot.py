"""Everything the facts of one conversation are computed from, in three reads.

`FactsLoader` runs on every turn, so it must not walk the schema one table at a
time. The three statements below cover the stored state, the contact, the active
order with its latest payment proof, and the order's lines with their products.

This is not a `BaseRepository`: it spans six tables. The Constitution's rule is
still structural here - the tenant is a constructor argument, and every table in
every statement carries `tenant_id = :tenant` in its WHERE or its ON clause, so a
conversation id from another business reads as an empty conversation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.conversation_state import ConversationState
from app.models.orders import Order, OrderItem, PaymentProof


@dataclass(frozen=True)
class OrderLine:
    """One order line and what the catalogue says about its product."""

    item: OrderItem
    # False when the product is gone: asking for an address is the safe side.
    es_digital: bool


@dataclass(frozen=True)
class ConversationSnapshot:
    """The raw rows behind a conversation's facts."""

    state: ConversationState | None = None
    contact: Contact | None = None
    order: Order | None = None
    last_proof: PaymentProof | None = None
    # When the business last rejected a proof of the active order.
    last_rejection_at: datetime | None = None
    lines: list[OrderLine] = field(default_factory=list)


class ConversationSnapshotRepository:
    """Reads the rows the facts of one tenant's conversations come from."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    def load(self, conversation: Conversation) -> ConversationSnapshot:
        """Read the snapshot of a conversation in at most three statements.

        A conversation from another tenant (or one that does not exist) yields
        an empty snapshot rather than an error: it is indistinguishable from a
        conversation nobody has written to yet.
        """
        if conversation.tenant_id != self._tenant_id:
            return ConversationSnapshot()

        state, contact = self._state_and_contact(conversation)
        order, proof, rejected_at = self._active_order(conversation.id)
        lines = self._lines(order.id) if order is not None else []
        return ConversationSnapshot(
            state=state,
            contact=contact,
            order=order,
            last_proof=proof,
            last_rejection_at=rejected_at,
            lines=lines,
        )

    # --------------------------------------------------------------- queries

    def _state_and_contact(
        self, conversation: Conversation
    ) -> tuple[ConversationState | None, Contact | None]:
        """Statement 1: the state row and the contact, together.

        The state is the outer side: a conversation that never had a turn has a
        contact and no state, and must still come back.
        """
        tenant = self._tenant_id
        state_on = and_(
            ConversationState.conversation_id == conversation.id,
            ConversationState.tenant_id == tenant,
        )
        if conversation.contact_id is None:
            # The simulator talks without a contact (until its synthetic one).
            only_state = select(ConversationState).where(
                ConversationState.conversation_id == conversation.id,
                ConversationState.tenant_id == tenant,
            )
            return self._session.execute(only_state).scalar_one_or_none(), None

        statement = (
            select(Contact, ConversationState)
            .select_from(Contact)
            .outerjoin(ConversationState, state_on)
            .where(Contact.id == conversation.contact_id, Contact.tenant_id == tenant)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None, None
        return row[1], row[0]

    def _active_order(
        self, conversation_id: uuid.UUID
    ) -> tuple[Order | None, PaymentProof | None, datetime | None]:
        """Statement 2: the active order, its latest proof and its last rejection.

        The active order is the newest one that was not discarded (C-06). The
        proof and the rejection time come from correlated sub-selects so they
        ride on the same round trip.
        """
        tenant = self._tenant_id
        latest_proof_id = (
            select(PaymentProof.id)
            .where(PaymentProof.order_id == Order.id, PaymentProof.tenant_id == tenant)
            .order_by(PaymentProof.created_at.desc())
            .limit(1)
            .correlate(Order)
            .scalar_subquery()
        )
        last_rejection = (
            select(func.max(PaymentProof.reviewed_at))
            .where(
                PaymentProof.order_id == Order.id,
                PaymentProof.tenant_id == tenant,
                PaymentProof.estado == "rechazado",
            )
            .correlate(Order)
            .scalar_subquery()
        )
        statement = (
            select(Order, PaymentProof, last_rejection)
            .select_from(Order)
            .outerjoin(PaymentProof, PaymentProof.id == latest_proof_id)
            .where(
                Order.tenant_id == tenant,
                Order.conversation_id == conversation_id,
                Order.estado != "descartado",
            )
            .order_by(Order.created_at.desc(), Order.numero.desc())
            .limit(1)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None, None, None
        return row[0], row[1], row[2]

    def _lines(self, order_id: uuid.UUID) -> list[OrderLine]:
        """Statement 3: the order's lines with their product's `es_digital`."""
        tenant = self._tenant_id
        statement = (
            select(OrderItem, Product.es_digital)
            .outerjoin(
                Product, and_(Product.id == OrderItem.product_id, Product.tenant_id == tenant)
            )
            .where(OrderItem.order_id == order_id, OrderItem.tenant_id == tenant)
            .order_by(OrderItem.created_at, OrderItem.id)
        )
        return [
            OrderLine(item=item, es_digital=bool(es_digital))
            for item, es_digital in self._session.execute(statement).all()
        ]
