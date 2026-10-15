"""Business profile and agent configuration (TASK-010).

Concurrency here is **last-write-wins with a warning**, not a lock
(Clarification 008). Two people editing the same business is rare and the
data is small; a pessimistic lock would mean one of them staring at a form
they cannot save, which is a worse failure than the one it prevents.

But silently discarding somebody's edit is not acceptable either. So a client
may send the `updated_at` it last saw. The write always succeeds - that is
what last-write-wins means - and the response says whether it landed on top of
a newer version, so the panel can tell the user "somebody else saved changes
while you were editing" instead of pretending nothing happened.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.business import AgentConfig, BusinessProfile
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.situation import SituationRepository
from app.services.situations import SituationService


@dataclass(frozen=True)
class SaveOutcome[T]:
    """What was written, and whether it overwrote somebody else's work."""

    record: T
    # True when the caller's `expected_updated_at` was older than what was
    # stored, meaning a concurrent save happened in between.
    overwrote_newer: bool


class WizardService:
    """Reads and writes the two configuration singletons for one tenant."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._profiles = BusinessProfileRepository(session, tenant_id)
        self._agents = AgentConfigRepository(session, tenant_id)

    # ---------------------------------------------------------- read

    def migrated_rules(self) -> str | None:
        """The old free-text rules, if the migrated situation is still on."""
        situation = SituationRepository(self._session, self._tenant_id).migrated()
        return situation.que_hace if situation and situation.estado == "activa" else None

    def business_profile(self) -> BusinessProfile | None:
        return self._profiles.current()

    def agent_config(self) -> AgentConfig | None:
        return self._agents.current()

    # --------------------------------------------------------- write

    def save_business_profile(
        self, *, expected_updated_at: datetime | None = None, **values: Any
    ) -> SaveOutcome[BusinessProfile]:
        stale = self._is_stale(self._profiles.current(), expected_updated_at)
        record = self._profiles.upsert(**values)
        self._session.commit()
        return SaveOutcome(record=record, overwrote_newer=stale)

    def save_agent_config(
        self, *, expected_updated_at: datetime | None = None, **values: Any
    ) -> SaveOutcome[AgentConfig]:
        # `is_active` is deliberately not settable here: the contract routes
        # activation through `/agent/activate`, which has preconditions the
        # configuration form does not check.
        values.pop("is_active", None)
        stale = self._is_stale(self._agents.current(), expected_updated_at)
        # The old free-text rules live on as the migrated situation (017, RF-011)
        # until the wizard stops asking for them (TASK-468).
        if "restrictions" in values:
            SituationService(self._session, self._tenant_id).set_migrated_text(
                values.pop("restrictions")
            )
        record = self._agents.upsert(**values)
        self._session.commit()
        return SaveOutcome(record=record, overwrote_newer=stale)

    @staticmethod
    def _is_stale(current: Any, expected_updated_at: datetime | None) -> bool:
        """Whether the caller was editing an older version than the stored one.

        Absent `expected_updated_at`, the answer is no: a client that does not
        participate in the check is not warned about it. Absent a stored row,
        also no - there is nothing to overwrite.
        """
        if expected_updated_at is None or current is None:
            return False
        stored: datetime = current.updated_at
        # Strict inequality, with no tolerance window. ISO 8601 carries
        # microseconds and pydantic parses them back, so a client echoing what
        # it was given compares exactly equal and is not warned. A tolerance
        # would only suppress the warning for two saves close together in
        # time - which is precisely when a collision is most likely.
        return stored > expected_updated_at
