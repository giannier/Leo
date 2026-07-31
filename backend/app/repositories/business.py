"""Business profile and agent configuration.

Both are one row per tenant, keyed by `tenant_id` rather than by a UUID of
their own, so they are extensions of `tenants` rather than collections. The
row is created on first write: a tenant that has not opened the wizard yet
has no profile, and inventing an empty one at registration would make
"configured" and "not configured" indistinguishable.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.models.business import AgentConfig, BusinessProfile
from app.repositories.base import BaseRepository


class _SingletonPerTenantRepository[ModelT: Any](BaseRepository[ModelT], abstract=True):
    """Shared behaviour for the one-row-per-tenant configuration tables."""

    def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Not available: these tables have no `id` of their own."""
        raise NotImplementedError(
            f"{type(self).__name__} is keyed by tenant_id, not by a row id. "
            "Use current() to read and upsert() to write."
        )

    def current(self) -> ModelT | None:
        """This tenant's row, or None if the wizard has not written one."""
        return self._session.execute(self._scoped()).scalar_one_or_none()

    def upsert(self, **values: Any) -> ModelT:
        """Create the row or update the fields supplied.

        Only the keys present are written. A PUT that omits a field leaves it
        alone rather than nulling it, so a partial save from one wizard step
        cannot erase what another step already collected.
        """
        instance = self.current()
        if instance is None:
            return self.add(**values)
        for field, value in values.items():
            setattr(instance, field, value)
        self._session.flush()
        return instance


class BusinessProfileRepository(_SingletonPerTenantRepository[BusinessProfile]):
    """The tenant's own identity: name, industry, hours, currency."""

    model = BusinessProfile


class AgentConfigRepository(_SingletonPerTenantRepository[AgentConfig]):
    """The agent's personality and guardrails for this tenant."""

    model = AgentConfig

    def set_active(self, *, is_active: bool, activated_at: Any = None) -> AgentConfig:
        """Flip the global on/off switch.

        Separate from `upsert` because the contract routes it through
        `/agent/activate` rather than the configuration PUT: turning the agent
        on is an action with preconditions (the activation checklist), not a
        field somebody edits in a form.
        """
        config = self.upsert(is_active=is_active)
        if is_active and config.activated_first_at is None and activated_at is not None:
            config.activated_first_at = activated_at
            self._session.flush()
        return config
