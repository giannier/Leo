"""The model's circuit breaker, per tenant (Feature 004, TASK-445, C-02).

Three failed model calls within 60 seconds open the circuit for two minutes. While
it is open no model is called for that tenant: the customer gets the business's
`unsupported_fallback` and the business is told once per opening (`llm_caido`,
emailed at most once an hour by `TenantAlerts.llm_down`). After two minutes the
next turn tries the model again.

The state lives in Redis so every worker sees it, through the same narrow
`LockStore` slice the locks use (get, set, delete). Two workers failing at the same
instant may count one failure fewer; the breaker is a guard against a provider that
is down, not an exact counter.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable

from app.core.locks import LockStore

FAILURES_TO_OPEN = 3
WINDOW_SECONDS = 60
OPEN_SECONDS = 120


def _failures_key(tenant_id: uuid.UUID) -> str:
    return f"llm:failures:{tenant_id}"


def _open_key(tenant_id: uuid.UUID) -> str:
    return f"llm:open:{tenant_id}"


class CircuitBreaker:
    def __init__(
        self,
        store: LockStore,
        tenant_id: uuid.UUID,
        *,
        on_open: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._on_open = on_open
        self._clock = clock

    async def is_open(self) -> bool:
        return await self._store.get(_open_key(self._tenant_id)) is not None

    async def record_failure(self) -> bool:
        """Count one failed call. True when this failure opened the circuit."""
        now = self._clock()
        raw = await self._store.get(_failures_key(self._tenant_id))
        try:
            stored = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw)) if raw else []
        except ValueError:
            stored = []
        recent = [float(t) for t in stored if now - float(t) < WINDOW_SECONDS] + [now]
        if len(recent) < FAILURES_TO_OPEN:
            await self._store.set(
                _failures_key(self._tenant_id), json.dumps(recent), ex=WINDOW_SECONDS
            )
            return False
        await self._store.delete(_failures_key(self._tenant_id))
        opened = await self._store.set(_open_key(self._tenant_id), "1", ex=OPEN_SECONDS, nx=True)
        if opened and self._on_open is not None:
            self._on_open()
        return bool(opened)
