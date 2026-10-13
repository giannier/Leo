"""Orders, their items, payment proofs and the order counter (Feature 004).

Every read starts from `_scoped()`, so a tenant never sees another's orders even
when it holds a real order id. The order counter is the one place that writes
with raw `INSERT ... ON CONFLICT`: it has to be atomic, and it stamps the
repository's own tenant itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.channel import Contact
from app.models.orders import (
    ORDERS_SEQUENCE,
    Order,
    OrderItem,
    PaymentProof,
    TenantSequence,
)
from app.repositories.base import BaseRepository


class TenantSequenceRepository(BaseRepository[TenantSequence]):
    """The named counters of one tenant."""

    model = TenantSequence

    def get(self, entity_id: uuid.UUID) -> TenantSequence | None:
        """Not available: rows are keyed by `(tenant_id, name)`, not an id."""
        raise NotImplementedError(
            f"{type(self).__name__} is keyed by name, not by a row id. "
            "Use current() to read and next_value() to advance."
        )

    def current(self, name: str) -> int:
        """The last value handed out for `name`, or 0 if none was."""
        statement = self._scoped().where(TenantSequence.name == name)
        row = self._session.execute(statement).scalar_one_or_none()
        return 0 if row is None else row.value

    def next_value(self, name: str) -> int:
        """Advance the counter and return the new value; the first call returns 1.

        One atomic statement: two concurrent callers of the same tenant queue on
        the row lock and get consecutive values, never the same one. A
        read-then-write here would hand two orders the same number.
        """
        statement = (
            pg_insert(TenantSequence)
            .values(tenant_id=self._tenant_id, name=name, value=1)
            .on_conflict_do_update(
                index_elements=[TenantSequence.tenant_id, TenantSequence.name],
                set_={"value": TenantSequence.value + 1},
            )
            .returning(TenantSequence.value)
        )
        return self._session.execute(statement).scalar_one()


class OrderRepository(BaseRepository[Order]):
    """The orders of one tenant."""

    model = Order

    def add_numbered(self, **values: Any) -> Order:
        """Create an order with the next number of this tenant's sequence."""
        if "numero" in values:
            raise ValueError("numero is assigned from the tenant sequence, not passed in")
        numero = TenantSequenceRepository(self._session, self._tenant_id).next_value(
            ORDERS_SEQUENCE
        )
        return self.add(numero=numero, **values)

    def get_for_update(self, order_id: uuid.UUID) -> Order | None:
        """The order, locked until the transaction ends.

        The operator's confirm and reject read the state and then change it; two
        clicks (or two operators) at once must queue here, so the second one sees the
        first one's result and gets a 409 instead of confirming twice.
        """
        statement = self._scoped().where(Order.id == order_id).with_for_update()
        return self._session.execute(statement).scalar_one_or_none()

    def search(
        self,
        *,
        estados: Collection[str] = (),
        since: datetime | None = None,
        until: datetime | None = None,
        q: str | None = None,
        sandbox: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[tuple[Order, Contact]], int]:
        """Orders for the list page, newest first, with their customer, and the total.

        `q` matches the order number, the customer's name (the one they gave or the
        one WhatsApp shows) or part of their phone. Sandbox orders are hidden unless
        asked for (C-19).
        """
        criteria = self._list_criteria(estados, since, until, q, sandbox)
        base = (
            select(Order, Contact)
            .join(
                Contact, and_(Contact.id == Order.contact_id, Contact.tenant_id == self._tenant_id)
            )
            .where(Order.tenant_id == self._tenant_id, *criteria)
        )
        total = self._session.execute(
            select(func.count())
            .select_from(Order)
            .join(
                Contact, and_(Contact.id == Order.contact_id, Contact.tenant_id == self._tenant_id)
            )
            .where(Order.tenant_id == self._tenant_id, *criteria)
        ).scalar_one()
        rows = self._session.execute(
            base.order_by(Order.created_at.desc(), Order.numero.desc()).limit(limit).offset(offset)
        ).all()
        return [(order, contact) for order, contact in rows], total

    def totals_by_state(self, *, since: datetime, until: datetime | None = None) -> dict[str, int]:
        """How many real orders were created in the period, by state."""
        statement = (
            select(Order.estado, func.count())
            .where(Order.tenant_id == self._tenant_id, Order.is_sandbox.is_(False))
            .where(Order.created_at >= since)
            .group_by(Order.estado)
        )
        if until is not None:
            statement = statement.where(Order.created_at < until)
        return {state: int(count) for state, count in self._session.execute(statement).all()}

    def sales_made(self, *, since: datetime, until: datetime | None = None) -> tuple[int, Decimal]:
        """`(count, total)` of the real orders that reached `confirmado` or beyond in the period."""
        statement = select(func.count(), func.coalesce(func.sum(Order.total), 0)).where(
            Order.tenant_id == self._tenant_id,
            Order.is_sandbox.is_(False),
            Order.estado.in_(("confirmado", "en_curso", "completado")),
            Order.confirmed_at >= since,
        )
        if until is not None:
            statement = statement.where(Order.confirmed_at < until)
        count, total = self._session.execute(statement).one()
        return int(count), Decimal(total)

    def _list_criteria(
        self,
        estados: Collection[str],
        since: datetime | None,
        until: datetime | None,
        q: str | None,
        sandbox: bool,
    ) -> list[ColumnElement[bool]]:
        criteria: list[ColumnElement[bool]] = []
        if not sandbox:
            criteria.append(Order.is_sandbox.is_(False))
        if estados:
            criteria.append(Order.estado.in_(list(estados)))
        if since is not None:
            criteria.append(Order.created_at >= since)
        if until is not None:
            criteria.append(Order.created_at < until)
        text_q = (q or "").strip()
        if text_q:
            escaped = text_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            matches: list[ColumnElement[bool]] = [
                Contact.custom_data["nombres"].astext.ilike(pattern, escape="\\"),
                Contact.profile_name.ilike(pattern, escape="\\"),
                Contact.phone_e164.ilike(pattern, escape="\\"),
            ]
            if text_q.lstrip("#").isdigit():
                matches.append(Order.numero == int(text_q.lstrip("#")))
            criteria.append(or_(*matches))
        return criteria

    def by_numero(self, numero: int) -> Order | None:
        statement = self._scoped().where(Order.numero == numero)
        return self._session.execute(statement).scalar_one_or_none()

    def for_conversation(self, conversation_id: uuid.UUID) -> Sequence[Order]:
        """Every order of one conversation, newest first."""
        statement = (
            self._scoped()
            .where(Order.conversation_id == conversation_id)
            .order_by(Order.created_at.desc(), Order.numero.desc())
        )
        return self._session.execute(statement).scalars().all()

    def active_for_conversation(self, conversation_id: uuid.UUID) -> Order | None:
        """The last order of the conversation that was not discarded (C-06).

        `numero` breaks ties: it only grows, and two orders written in one
        transaction share `created_at`.
        """
        statement = (
            self._scoped()
            .where(Order.conversation_id == conversation_id, Order.estado != "descartado")
            .order_by(Order.created_at.desc(), Order.numero.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()


class OrderItemRepository(BaseRepository[OrderItem]):
    """The lines of the orders of one tenant."""

    model = OrderItem

    def for_order(self, order_id: uuid.UUID) -> Sequence[OrderItem]:
        """The lines of one order, in the order they were added."""
        statement = (
            self._scoped()
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.created_at, OrderItem.id)
        )
        return self._session.execute(statement).scalars().all()

    def for_orders(self, order_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[OrderItem]]:
        """The lines of several orders in one query, grouped by order."""
        grouped: dict[uuid.UUID, list[OrderItem]] = {order_id: [] for order_id in order_ids}
        if not grouped:
            return grouped
        statement = (
            self._scoped()
            .where(OrderItem.order_id.in_(list(grouped)))
            .order_by(OrderItem.created_at, OrderItem.id)
        )
        for item in self._session.execute(statement).scalars():
            grouped[item.order_id].append(item)
        return grouped


class PaymentProofRepository(BaseRepository[PaymentProof]):
    """The payment receipts of one tenant."""

    model = PaymentProof

    def for_order(self, order_id: uuid.UUID) -> Sequence[PaymentProof]:
        """Every proof of one order, newest first."""
        statement = (
            self._scoped()
            .where(PaymentProof.order_id == order_id)
            .order_by(PaymentProof.created_at.desc())
        )
        return self._session.execute(statement).scalars().all()

    def for_orders(self, order_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[PaymentProof]]:
        """The proofs of several orders in one query, newest first, grouped by order."""
        grouped: dict[uuid.UUID, list[PaymentProof]] = {order_id: [] for order_id in order_ids}
        if not grouped:
            return grouped
        statement = (
            self._scoped()
            .where(PaymentProof.order_id.in_(list(grouped)))
            .order_by(PaymentProof.created_at.desc())
        )
        for proof in self._session.execute(statement).scalars():
            grouped[proof.order_id].append(proof)
        return grouped

    def latest_for_order(self, order_id: uuid.UUID) -> PaymentProof | None:
        statement = (
            self._scoped()
            .where(PaymentProof.order_id == order_id)
            .order_by(PaymentProof.created_at.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def code_seen(self, codigo: str, *, sandbox: bool, exclude_id: uuid.UUID | None = None) -> bool:
        """Whether another proof of this tenant carries the same operation code.

        Compared case-insensitively, and only among orders of the same kind: a
        simulator proof never counts against a real one (C-19), while the simulator
        can still show the flag against its own earlier proofs.
        """
        statement = (
            select(PaymentProof.id)
            .join(Order, Order.id == PaymentProof.order_id)
            .where(
                PaymentProof.tenant_id == self._tenant_id,
                Order.tenant_id == self._tenant_id,
                Order.is_sandbox.is_(sandbox),
                func.lower(PaymentProof.codigo_operacion) == codigo.lower(),
            )
            .limit(1)
        )
        if exclude_id is not None:
            statement = statement.where(PaymentProof.id != exclude_id)
        return self._session.execute(statement).first() is not None
