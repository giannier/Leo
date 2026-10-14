"""Persistent conversation state with optimistic concurrency (Feature 004).

`conversation_state` holds one row per conversation: the current stage, the
funnel column and the stored facts. Two things can write it at nearly the same
time - the turn that is answering and an operator confirming a payment - so a
save carries the version it read (`UPDATE ... WHERE version = :v`). A collision
is retried once on top of the fresh row; a second one is raised, never ignored.

Every change of stage or funnel column also leaves an event
(`conversation_stage_events`), and only a change does: saving the same stage
twice writes no second event.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.conversation_state import (
    ConversationStageEvent,
    ConversationState,
    FunnelCode,
)
from app.process.facts import STORED_KEYS, Facts
from app.process.templates.base import Stage, Template
from app.repositories.base import TenantScopeError
from app.repositories.conversation_state import (
    ConversationStageEventRepository,
    ConversationStateRepository,
)

# A conversation starts at the first stage of every template.
INITIAL_STAGE = Stage.INICIO
INITIAL_FUNNEL = FunnelCode.NUEVO

# What the event snapshot leaves out: personal data (C-20).
_SNAPSHOT_EXCLUDE: dict[str, Any] = {"nombre_cliente": True, "derivado": {"resumen"}}


class StateConflict(RuntimeError):
    """The state changed under the save twice in a row."""


@dataclass(frozen=True)
class SaveResult:
    """What a save did."""

    state: ConversationState
    # The event written because the stage or the funnel column changed, if any.
    event: ConversationStageEvent | None
    # True if the first attempt collided and the second one went through.
    retried: bool

    @property
    def changed_stage(self) -> bool:
        return self.event is not None


def facts_snapshot(facts: Facts) -> dict[str, Any]:
    """The facts as an event records them: stored and derived, without personal data.

    The customer's name and the handover summary (which a model wrote from what
    the customer said) stay out. The data the customer still owes - `dni`,
    `correo` - appears only as the *names* of the missing fields.
    """
    return facts.model_dump(mode="json", exclude=_SNAPSHOT_EXCLUDE)


class StateRepo:
    """Reads and writes the state of the conversations of one tenant."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._states = ConversationStateRepository(session, tenant_id)
        self._events = ConversationStageEventRepository(session, tenant_id)

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    # ------------------------------------------------------------------ read

    def get(self, conversation_id: uuid.UUID) -> ConversationState | None:
        return self._states.by_conversation(conversation_id)

    def get_or_create(self, conversation: Conversation, template: Template) -> ConversationState:
        """The state of a conversation, creating it at INICIO on the first turn.

        The conversation must already have been read through a tenant-scoped
        repository: `conversation_state` has no composite foreign key, so this
        check is what keeps one business from creating state for another's
        conversation.

        No event is written on creation: an event records a *change*, and the
        first one is the move from INICIO to wherever the first turn lands.
        """
        if conversation.tenant_id != self._tenant_id:
            raise TenantScopeError(
                f"Conversation {conversation.id!s} belongs to tenant "
                f"{conversation.tenant_id!s}, not to {self._tenant_id!s}"
            )
        existing = self.get(conversation.id)
        if existing is not None:
            return existing

        # Two workers can both find nothing; the primary key picks one winner and
        # the other falls through to the read below instead of failing.
        self._session.execute(
            pg_insert(ConversationState)
            .values(
                conversation_id=conversation.id,
                tenant_id=self._tenant_id,
                template_key=template.key,
                template_version=template.version,
                stage_key=INITIAL_STAGE.value,
                funnel_code=INITIAL_FUNNEL.value,
                is_sandbox=conversation.channel == "sandbox",
            )
            .on_conflict_do_nothing(index_elements=[ConversationState.conversation_id])
        )
        created = self._states.by_conversation(conversation.id)
        if created is None:  # pragma: no cover - the insert above just ran
            raise LookupError(f"state for conversation {conversation.id!s} could not be created")
        return created

    # ----------------------------------------------------------------- write

    def save(
        self,
        conversation_id: uuid.UUID,
        *,
        stage: Stage,
        funnel: FunnelCode,
        facts_patch: Mapping[str, Any] | None,
        reason: str,
        expected_version: int,
        snapshot: Mapping[str, Any] | None = None,
        force_event: bool = False,
        retry: bool = True,
    ) -> SaveResult:
        """Write the stage, the funnel column and a patch of the stored facts.

        `facts_patch` is merged key by key over the stored facts; a key set to
        `None` is removed. Only stored facts may be patched (C-03): a derived
        fact written here would drift from its source.

        `expected_version` is the version the caller read. If the row has moved
        on, the save is retried once on top of the fresh row - the patch is the
        caller's intent and is re-applied - and raises `StateConflict` if it
        collides again. `retry=False` makes it a strict compare-and-set: a claim
        (a follow-up attempt, TASK-444) must lose to whoever wrote first.

        `force_event` records the event even when neither the stage nor the funnel
        column moved. An operator's decision (rejecting a receipt leaves the stage in
        VERIFICACION) is part of the history whether or not the stage changed.
        """
        patch = dict(facts_patch or {})
        unknown = set(patch) - STORED_KEYS
        if unknown:
            raise ValueError(
                f"{sorted(unknown)} are not stored facts; derived facts are never written (C-03)"
            )
        if not reason.strip():
            raise ValueError("a state change needs a reason")

        version = expected_version
        for attempt in (1, 2):
            written = self._try_save(
                conversation_id,
                stage=stage,
                funnel=funnel,
                patch=patch,
                reason=reason,
                version=version,
                snapshot=snapshot,
                force_event=force_event,
            )
            if written is not None:
                state, event = written
                return SaveResult(state=state, event=event, retried=attempt == 2)
            if not retry:
                break
            fresh = self._read_fresh(conversation_id)
            version = fresh.version
        raise StateConflict(
            f"the state of conversation {conversation_id!s} changed twice during the save"
        )

    # --------------------------------------------------------------- helpers

    def _read_fresh(self, conversation_id: uuid.UUID) -> ConversationState:
        """The row as it is in the database now, ignoring what the session cached."""
        statement = (
            select(ConversationState)
            .where(
                ConversationState.conversation_id == conversation_id,
                ConversationState.tenant_id == self._tenant_id,
            )
            .execution_options(populate_existing=True)
        )
        row = self._session.execute(statement).scalar_one_or_none()
        if row is None:
            raise LookupError(f"conversation {conversation_id!s} has no state to save")
        return row

    def _try_save(
        self,
        conversation_id: uuid.UUID,
        *,
        stage: Stage,
        funnel: FunnelCode,
        patch: dict[str, Any],
        reason: str,
        version: int,
        snapshot: Mapping[str, Any] | None,
        force_event: bool = False,
    ) -> tuple[ConversationState, ConversationStageEvent | None] | None:
        """One attempt: None means the version moved and nothing was written."""
        current = self._read_fresh(conversation_id)
        if current.version != version:
            return None

        before = (current.stage_key, current.funnel_code)
        changed = before != (stage.value, funnel.value)
        facts = {**current.facts, **patch}
        facts = {key: value for key, value in facts.items() if value is not None}

        values: dict[str, Any] = {
            "stage_key": stage.value,
            "funnel_code": funnel.value,
            "facts": facts,
            "version": ConversationState.version + 1,
            "updated_at": func.clock_timestamp(),
        }
        if changed:
            values["stage_entered_at"] = func.clock_timestamp()
        statement = (
            update(ConversationState)
            .where(
                ConversationState.conversation_id == conversation_id,
                ConversationState.tenant_id == self._tenant_id,
                ConversationState.version == version,
            )
            .values(**values)
            .returning(ConversationState)
            .execution_options(populate_existing=True, synchronize_session=False)
        )
        updated = self._session.execute(statement).scalar_one_or_none()
        if updated is None:
            return None

        event: ConversationStageEvent | None = None
        if changed or force_event:
            event = self._events.add(
                conversation_id=conversation_id,
                from_stage=before[0],
                to_stage=stage.value,
                from_funnel=before[1],
                to_funnel=funnel.value,
                reason=reason,
                facts_snapshot=dict(snapshot) if snapshot is not None else None,
            )
        return updated, event
