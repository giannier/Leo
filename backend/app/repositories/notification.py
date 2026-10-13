"""The notices addressed to one tenant's business (Feature 004, TASK-431)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.models.tenant import TenantNotification
from app.repositories.base import BaseRepository


class TenantNotificationRepository(BaseRepository[TenantNotification]):
    """The notices of one tenant: events that repeat and notices sent once."""

    model = TenantNotification

    def record(self, kind: str, payload: dict[str, Any] | None = None) -> TenantNotification:
        """Add one notice. Flushed, not committed: it lives and dies with its caller's
        transaction, so a notice never describes something that was rolled back."""
        return self.add(kind=kind, payload=payload or {})

    def recent(self, kind: str | None = None, *, limit: int = 50) -> Sequence[TenantNotification]:
        """The newest notices, optionally of one kind."""
        statement = self._scoped().order_by(
            TenantNotification.created_at.desc(), TenantNotification.id
        )
        if kind is not None:
            statement = statement.where(TenantNotification.kind == kind)
        return self._session.execute(statement.limit(limit)).scalars().all()

    def emailed_since(self, kind: str, since: datetime) -> bool:
        """Whether a notice of this kind already sent its email since `since`.

        Notices that limit their email (`llm_caido`: once an hour) mark the rows
        that did send with `payload.correo = true`.
        """
        statement = (
            select(func.count())
            .select_from(TenantNotification)
            .where(
                TenantNotification.tenant_id == self._tenant_id,
                TenantNotification.kind == kind,
                TenantNotification.created_at >= since,
                TenantNotification.payload["correo"].astext == "true",
            )
        )
        return self._session.execute(statement).scalar_one() > 0

    def lock_kind(self, kind: str) -> None:
        """Serialise the writers of one kind for this tenant until the transaction ends.

        The rate limit of an email is "read the last one, then write a new one": two
        workers at once would both read nothing and both send. A transaction-level
        advisory lock on (tenant, kind) makes the second wait for the first's commit.
        """
        digest = hashlib.blake2b(f"{self._tenant_id}:{kind}".encode(), digest_size=8).digest()
        key = int.from_bytes(digest, "big", signed=True)
        self._session.execute(select(func.pg_advisory_xact_lock(key)))
