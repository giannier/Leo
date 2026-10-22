"""Returns and order documents of one business (feature 019, phase G)."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select

from app.models.aftersale import (
    OPEN_RETURN_STATES,
    RETURNS_SEQUENCE,
    OrderDocument,
    OrderReturn,
    OrderReturnEvent,
)
from app.models.channel import Contact
from app.models.orders import Order
from app.repositories.base import BaseRepository
from app.repositories.orders import TenantSequenceRepository


class ReturnRepository(BaseRepository[OrderReturn]):
    model = OrderReturn

    def add_numbered(self, **values: Any) -> OrderReturn:
        """A return with the next number of this business's sequence."""
        if "numero" in values:
            raise ValueError("numero is assigned from the tenant sequence, not passed in")
        numero = TenantSequenceRepository(self._session, self._tenant_id).next_value(
            RETURNS_SEQUENCE
        )
        return self.add(numero=numero, **values)

    def get_for_update(self, return_id: uuid.UUID) -> OrderReturn | None:
        """Locked until the end of the transaction: two people deciding at once queue here."""
        statement = self._scoped().where(OrderReturn.id == return_id).with_for_update()
        return self._session.execute(statement).scalar_one_or_none()

    def open_for(self, order_id: uuid.UUID, tipo: str) -> OrderReturn | None:
        statement = self._scoped().where(
            OrderReturn.order_id == order_id,
            OrderReturn.tipo == tipo,
            OrderReturn.estado.in_(OPEN_RETURN_STATES),
        )
        return self._session.execute(statement).scalar_one_or_none()

    def of_orders(self, order_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[OrderReturn]]:
        """The returns of each order, newest first."""
        if not order_ids:
            return {}
        statement = (
            self._scoped()
            .where(OrderReturn.order_id.in_(list(order_ids)))
            .order_by(OrderReturn.created_at.desc())
        )
        grouped: dict[uuid.UUID, list[OrderReturn]] = {}
        for row in self._session.execute(statement).scalars():
            grouped.setdefault(row.order_id, []).append(row)
        return grouped

    def search(
        self,
        *,
        estados: Collection[str] = (),
        order_id: uuid.UUID | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> tuple[list[tuple[OrderReturn, Order, Contact]], int]:
        """Returns for the panel, open ones first and then newest first, with order and customer.

        Returns of simulator orders never show: they are not business.
        """
        criteria = [OrderReturn.tenant_id == self._tenant_id, Order.is_sandbox.is_(False)]
        if estados:
            criteria.append(OrderReturn.estado.in_(list(estados)))
        if order_id is not None:
            criteria.append(OrderReturn.order_id == order_id)
        joined = (
            select(OrderReturn, Order, Contact)
            .join(Order, and_(Order.id == OrderReturn.order_id, Order.tenant_id == self._tenant_id))
            .join(
                Contact,
                and_(Contact.id == OrderReturn.contact_id, Contact.tenant_id == self._tenant_id),
            )
            .where(*criteria)
        )
        total = self._session.execute(
            select(func.count())
            .select_from(OrderReturn)
            .join(Order, and_(Order.id == OrderReturn.order_id, Order.tenant_id == self._tenant_id))
            .where(*criteria)
        ).scalar_one()
        # `solicitada` first: it is the work that waits for a person.
        waiting_first = (OrderReturn.estado != "solicitada").asc()
        rows = self._session.execute(
            joined.order_by(waiting_first, OrderReturn.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [(r, o, c) for r, o, c in rows], total

    def count_waiting(self) -> int:
        """Real returns still waiting for a decision (the badge of the panel)."""
        return self._session.execute(
            select(func.count())
            .select_from(OrderReturn)
            .join(Order, and_(Order.id == OrderReturn.order_id, Order.tenant_id == self._tenant_id))
            .where(
                OrderReturn.tenant_id == self._tenant_id,
                OrderReturn.estado == "solicitada",
                Order.is_sandbox.is_(False),
            )
        ).scalar_one()

    def counts_between(self, since: datetime, until: datetime) -> dict[str, int]:
        """Real returns opened in the period, by state."""
        statement = (
            select(OrderReturn.estado, func.count())
            .join(Order, and_(Order.id == OrderReturn.order_id, Order.tenant_id == self._tenant_id))
            .where(
                OrderReturn.tenant_id == self._tenant_id,
                Order.is_sandbox.is_(False),
                OrderReturn.created_at >= since,
                OrderReturn.created_at < until,
            )
            .group_by(OrderReturn.estado)
        )
        return {str(state): int(n) for state, n in self._session.execute(statement).all()}


class ReturnEventRepository(BaseRepository[OrderReturnEvent]):
    model = OrderReturnEvent

    def of_return(self, return_id: uuid.UUID) -> Sequence[OrderReturnEvent]:
        statement = (
            self._scoped()
            .where(OrderReturnEvent.return_id == return_id)
            .order_by(OrderReturnEvent.created_at, OrderReturnEvent.id)
        )
        return self._session.execute(statement).scalars().all()


class DocumentRepository(BaseRepository[OrderDocument]):
    model = OrderDocument

    def of_order(
        self, order_id: uuid.UUID, *, include_void: bool = True
    ) -> Sequence[OrderDocument]:
        statement = self._scoped().where(OrderDocument.order_id == order_id)
        if not include_void:
            statement = statement.where(OrderDocument.estado == "emitido")
        statement = statement.order_by(OrderDocument.created_at.desc())
        return self._session.execute(statement).scalars().all()

    def of_orders(self, order_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[OrderDocument]]:
        """The documents that stand (not voided) of each order, newest first."""
        if not order_ids:
            return {}
        statement = (
            self._scoped()
            .where(OrderDocument.order_id.in_(list(order_ids)), OrderDocument.estado == "emitido")
            .order_by(OrderDocument.created_at.desc())
        )
        grouped: dict[uuid.UUID, list[OrderDocument]] = {}
        for row in self._session.execute(statement).scalars():
            grouped.setdefault(row.order_id, []).append(row)
        return grouped
