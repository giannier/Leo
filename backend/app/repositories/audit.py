"""The audit trail of one tenant's privileged actions (Feature 004, TASK-430).

`audit_logs` is append-only. This repository adds entries for a tenant and reads
the ones that concern a given order, always through the tenant filter.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """The audit entries of one tenant."""

    model = AuditLog

    def record(self, *, actor_id: uuid.UUID, action: str, detail: dict[str, Any]) -> AuditLog:
        """Append one entry. The caller commits it with the change it describes.

        `detail` must carry no personal data and no credential: the log is read far
        more widely than the table it describes.
        """
        return self.add(actor_id=actor_id, action=action, detail=detail)

    def for_order(self, order_id: uuid.UUID) -> Sequence[AuditLog]:
        """What operators did to one order, oldest first."""
        statement = (
            self._scoped()
            .where(AuditLog.detail["order_id"].astext == str(order_id))
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        return self._session.execute(statement).scalars().all()
