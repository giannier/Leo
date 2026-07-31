"""Business payment methods."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.business import PaymentMethodBusiness
from app.repositories.base import BaseRepository


class PaymentMethodRepository(BaseRepository[PaymentMethodBusiness]):
    """How this business gets paid, as the agent explains it to customers."""

    model = PaymentMethodBusiness

    def list_active(self) -> Sequence[PaymentMethodBusiness]:
        return self.list(PaymentMethodBusiness.is_active.is_(True))

    def count_active(self) -> int:
        """Only active methods satisfy the activation checklist.

        A deactivated method is one the business has stopped accepting, so
        counting it would let a tenant activate an agent that then quotes a
        payment route nobody honours.
        """
        return self.count(PaymentMethodBusiness.is_active.is_(True))

    def find_by_method(self, method: str) -> PaymentMethodBusiness | None:
        """Used to answer `409 method_already_exists` before inserting.

        The unique constraint on `(tenant_id, method)` is the real guarantee;
        this exists so the API can name the conflict instead of surfacing a
        database error.
        """
        statement = self._scoped().where(PaymentMethodBusiness.method == method)
        return self._session.execute(statement).scalar_one_or_none()
