"""The inbox's scheduled work (Feature 007, TASK-703 and TASK-705).

* `return_idle_takeovers`: a takeover nobody touched for the business's
  `auto_return_minutes` (30 by default, NULL = never) goes back to the agent, with
  an event in the thread (HU-02). Five minutes before, the panels get a
  `takeover.expiring` event so the holder can keep it.
* `notify_unattended`: a handover (`derivado`) nobody took within
  `unattended_queue_minutes` (10 by default) is emailed to the owner once (HU-03);
  the fact remembers it (`derivado.avisado`).

Platform-wide reads, then everything re-scoped to each tenant; one commit per
conversation, so one failure never stops the rest. Events go out after each commit.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, and_, cast, func, select
from sqlalchemy.orm import Session

from app.core.events import publish
from app.models.conversation import Conversation
from app.models.conversation_state import ConversationState, FunnelCode
from app.models.inbox import (
    DEFAULT_AUTO_RETURN_MINUTES,
    DEFAULT_UNATTENDED_MINUTES,
    NotificationSettings,
)
from app.process.facts import Derivado
from app.process.state import StateConflict, StateRepo
from app.process.templates.base import Stage
from app.providers.email.base import EmailMessage
from app.services.inbox import Inbox
from app.services.tenant_alerts import TenantAlerts

logger = logging.getLogger(__name__)
WARNING = timedelta(minutes=5)
_BATCH = 200


def idle_takeovers(
    session: Session, *, now: datetime, early: timedelta = timedelta(0)
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant, conversation) of takeovers idle for their limit (minus `early`).

    A business that set "never" (`auto_return_minutes` NULL with a row) is skipped;
    one without a row has the default.
    """
    minutes = func.coalesce(NotificationSettings.auto_return_minutes, DEFAULT_AUTO_RETURN_MINUTES)
    limit = func.make_interval(0, 0, 0, 0, 0, minutes) - early
    statement = (
        select(Conversation.tenant_id, Conversation.id)
        .outerjoin(NotificationSettings, NotificationSettings.tenant_id == Conversation.tenant_id)
        .where(
            Conversation.takeover_user_id.is_not(None),
            Conversation.takeover_activity_at.is_not(None),
            # A row with NULL is "never"; no row is the default.
            (NotificationSettings.tenant_id.is_(None))
            | (NotificationSettings.auto_return_minutes.is_not(None)),
            Conversation.takeover_activity_at + limit <= now,
        )
        .limit(_BATCH)
    )
    return [(row[0], row[1]) for row in session.execute(statement).all()]


def return_idle_takeovers(session: Session, *, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    done = 0
    expiring = set(idle_takeovers(session, now=moment, early=WARNING)) - set(
        idle := idle_takeovers(session, now=moment)
    )
    for tenant_id, conversation_id in expiring:
        publish(tenant_id, "takeover.expiring", conversation_id=conversation_id)
    for tenant_id, conversation_id in idle:
        try:
            Inbox(session, tenant_id, clock=lambda: moment).release(conversation_id, None)
            session.commit()
            publish(tenant_id, "conversation.updated", conversation_id=conversation_id)
            done += 1
        except Exception:  # noqa: BLE001 - one conversation never stops the rest
            session.rollback()
            logger.exception("auto-return failed", extra={"tenant_id": str(tenant_id)})
    return done


def unattended_handovers(session: Session, *, now: datetime) -> list[tuple[uuid.UUID, uuid.UUID]]:
    desde = cast(ConversationState.facts["derivado"]["desde"].astext, DateTime(timezone=True))
    hasta = cast(ConversationState.facts["derivado"]["hasta"].astext, DateTime(timezone=True))
    minutes = func.coalesce(
        NotificationSettings.unattended_queue_minutes, DEFAULT_UNATTENDED_MINUTES
    )
    statement = (
        select(ConversationState.tenant_id, ConversationState.conversation_id)
        .join(
            Conversation,
            and_(
                Conversation.id == ConversationState.conversation_id,
                Conversation.tenant_id == ConversationState.tenant_id,
            ),
        )
        .outerjoin(
            NotificationSettings, NotificationSettings.tenant_id == ConversationState.tenant_id
        )
        .where(
            ConversationState.is_sandbox.is_(False),
            ConversationState.facts.has_key("derivado"),
            ConversationState.facts["derivado"]["desde"].astext.is_not(None),
            ConversationState.facts["derivado"]["avisado"].astext.is_distinct_from("true"),
            Conversation.takeover_user_id.is_(None),
            Conversation.status == "open",
            hasta > now,
            desde + func.make_interval(0, 0, 0, 0, 0, minutes) <= now,
        )
        .limit(_BATCH)
    )
    return [(row[0], row[1]) for row in session.execute(statement).all()]


def notify_unattended(session: Session, *, now: datetime | None = None) -> list[EmailMessage]:
    """Record the notice, mark the handover as told; returns the emails to send after."""
    moment = now or datetime.now(UTC)
    emails: list[EmailMessage] = []
    for tenant_id, conversation_id in unattended_handovers(session, now=moment):
        try:
            states = StateRepo(session, tenant_id)
            state = states.get(conversation_id)
            if state is None or "derivado" not in state.facts:
                continue
            handover = Derivado.model_validate(state.facts["derivado"])
            settings = session.get(NotificationSettings, tenant_id)
            minutes = settings.unattended_queue_minutes if settings else DEFAULT_UNATTENDED_MINUTES
            states.save(
                conversation_id,
                stage=Stage(state.stage_key),
                funnel=FunnelCode(state.funnel_code),
                facts_patch={"derivado": {**state.facts["derivado"], "avisado": True}},
                reason="derivacion:sin_atender",
                expected_version=state.version,
                retry=False,
            )
            alerts = TenantAlerts(session, tenant_id)
            alerts.unattended(
                conversation_id, motivo=handover.motivo, resumen=handover.resumen, minutes=minutes
            )
            session.commit()
            emails.extend(alerts.pending_emails)
            publish(tenant_id, "conversation.updated", conversation_id=conversation_id)
        except StateConflict:
            session.rollback()
        except Exception:  # noqa: BLE001 - one conversation never stops the rest
            session.rollback()
            logger.exception("unattended notice failed", extra={"tenant_id": str(tenant_id)})
    return emails
