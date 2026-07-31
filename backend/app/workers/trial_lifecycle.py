"""Scheduled jobs for the trial lifecycle (TASK-019).

Four of them, from `contracts/api.md` §jobs:

* `trial_notifier` (daily) - the day -3 / -1 / 0 warnings.
* `trial_enforcer` (hourly) - expired trials become `paused`, with a farewell
  on any conversation still open.
* `tenant_archiver` (daily) - `paused` for more than 30 days becomes
  `archived`.
* `wizard_reminder` (daily) - one nudge for a checklist abandoned 24 hours ago.

Every job takes `now` as an argument, defaulting to the wall clock. One run
therefore sees a single, consistent "now" instead of re-reading a clock that
moves between the query and the decision, and a test can drive the whole
transition table without patching `datetime` globally.

Each tenant is committed on its own. A batch that failed halfway through and
rolled back would re-notify everyone it had already emailed on the next tick,
and one tenant with bad data must not stop the other four hundred from being
processed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.tenant import WIZARD_REMINDER, Tenant
from app.providers.email.base import EmailProvider
from app.services.activation import build_checklist, owner_of
from app.services.emails import (
    build_trial_notice_email,
    build_wizard_reminder_email,
)
from app.services.trial import (
    WIZARD_IDLE_HOURS,
    active_trials,
    archivable,
    archive_tenant,
    claim_notification,
    due_notice,
    expired_trials,
    is_expired,
    pause_tenant,
    trials_over_quota,
)

logger = logging.getLogger(__name__)

# What the reminder calls each unfinished item. Spanish, because it is copy
# the customer reads - the keys themselves stay English like the rest of the
# codebase.
PENDING_LABELS = {
    "email_verified": "Confirmar tu correo electronico",
    "business_profile_complete": "Completar los datos de tu negocio",
    "agent_config_complete": "Configurar la personalidad de tu agente",
    "has_active_product": "Cargar al menos un producto o servicio",
    "has_payment_method": "Indicar como te pueden pagar",
    "phone_verified": "Verificar tu telefono de contacto",
}


@dataclass(frozen=True)
class JobOutcome:
    """What one run did. Returned so a test can assert on it and ARQ logs it."""

    considered: int = 0
    acted: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return f"considered={self.considered} acted={self.acted} skipped={self.skipped}"


def _panel_url(settings: Settings, path: str) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}{path}"


async def _send(email_provider: EmailProvider | None, message: Any) -> bool:
    """Deliver one message, treating a failure as this tenant's problem only.

    A provider outage must not abort the run: the remaining tenants have
    notices of their own that are just as time-sensitive, and a job that dies
    on the first bad address never reaches them.
    """
    if email_provider is None:
        return False
    try:
        await email_provider.send(message)
    except Exception:
        logger.exception("notification could not be delivered")
        return False
    return True


async def run_trial_notifier(
    session: Session,
    *,
    email_provider: EmailProvider | None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> JobOutcome:
    """Send the day -3 / -1 / 0 warnings (RF-005)."""
    now = now or datetime.now(UTC)
    settings = settings or get_settings()
    considered = acted = skipped = 0

    for tenant in active_trials(session):
        considered += 1
        kind = due_notice(session, tenant, now=now)
        if kind is None:
            skipped += 1
            continue

        # Claimed before sending: a claim written afterwards leaves a window
        # where a crash between the two repeats the message tomorrow.
        if not claim_notification(session, tenant.id, kind):
            skipped += 1
            continue

        owner = owner_of(session, tenant.id)
        if owner is None:
            # Nobody to write to. The claim stands anyway - re-running daily
            # against an ownerless tenant would achieve nothing but load.
            session.commit()
            skipped += 1
            continue

        session.commit()
        await _send(
            email_provider,
            build_trial_notice_email(
                kind=kind,
                to=owner.email,
                tenant_name=tenant.name,
                panel_url=_panel_url(settings, "/planes"),
            ),
        )
        acted += 1

    return JobOutcome(considered=considered, acted=acted, skipped=skipped)


async def run_trial_enforcer(
    session: Session,
    *,
    email_provider: EmailProvider | None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> JobOutcome:
    """Pause trials that are over, by date or by quota (RF-005).

    Hourly rather than daily: the quota can run out at any moment, and a
    tenant that spent its last message at 09:05 should not keep answering
    free until midnight.
    """
    now = now or datetime.now(UTC)
    settings = settings or get_settings()
    considered = acted = skipped = 0

    # Both reasons, de-duplicated: a tenant can be over on date *and* quota,
    # and pausing it twice would send two farewells into the same thread.
    candidates: dict[Any, Tenant] = {}
    for tenant in expired_trials(session, now=now):
        candidates[tenant.id] = tenant
    for tenant in trials_over_quota(session):
        candidates[tenant.id] = tenant

    for tenant in candidates.values():
        considered += 1
        if not is_expired(session, tenant, now=now):
            skipped += 1
            continue

        pause_tenant(session, tenant, now=now)
        # The day-0 notice and the pause are the same event, so the claim is
        # taken here too. Without it the notifier would mail again tomorrow
        # about a trial that already ended today.
        should_notify = claim_notification(session, tenant.id, "trial_ended")
        owner = owner_of(session, tenant.id)
        session.commit()
        acted += 1

        if should_notify and owner is not None:
            await _send(
                email_provider,
                build_trial_notice_email(
                    kind="trial_ended",
                    to=owner.email,
                    tenant_name=tenant.name,
                    panel_url=_panel_url(settings, "/planes"),
                ),
            )

    return JobOutcome(considered=considered, acted=acted, skipped=skipped)


async def run_tenant_archiver(session: Session, *, now: datetime | None = None) -> JobOutcome:
    """Archive tenants paused for more than 30 days (RF-006).

    Soft. Nothing is deleted, and an admin can reverse it; permanent deletion
    happens only at the account holder's request (Ley 29733).
    """
    now = now or datetime.now(UTC)
    considered = acted = 0

    for tenant in archivable(session, now=now):
        considered += 1
        archive_tenant(tenant, now=now)
        session.commit()
        acted += 1

    return JobOutcome(considered=considered, acted=acted)


async def run_wizard_reminder(
    session: Session,
    *,
    email_provider: EmailProvider | None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> JobOutcome:
    """One nudge for a checklist left unfinished (§jobs).

    "Email unico" is enforced by the notification key, so a customer who
    ignores it is not chased weekly. One reminder is a service; a series of
    them is the reason people filter your domain.
    """
    now = now or datetime.now(UTC)
    settings = settings or get_settings()
    considered = acted = skipped = 0
    cutoff = now.timestamp() - WIZARD_IDLE_HOURS * 3600

    for tenant in active_trials(session):
        considered += 1
        owner = owner_of(session, tenant.id)
        if owner is None:
            skipped += 1
            continue

        # Idle, not merely young. A tenant that signed up an hour ago and is
        # still working through the wizard does not need an email about it.
        if tenant.updated_at.timestamp() > cutoff:
            skipped += 1
            continue

        checklist = build_checklist(session, tenant_id=tenant.id, user=owner)
        pending = [
            PENDING_LABELS[entry.key]
            for entry in checklist.items
            if entry.required and not entry.done
        ]
        if not pending:
            skipped += 1
            continue

        if not claim_notification(session, tenant.id, WIZARD_REMINDER):
            skipped += 1
            continue

        session.commit()
        await _send(
            email_provider,
            build_wizard_reminder_email(
                to=owner.email,
                tenant_name=tenant.name,
                pending=pending,
                panel_url=_panel_url(settings, "/onboarding"),
            ),
        )
        acted += 1

    return JobOutcome(considered=considered, acted=acted, skipped=skipped)


# --------------------------------------------------------------- ARQ entry points


async def trial_notifier(ctx: dict[str, Any]) -> str:
    with ctx["session_factory"]() as session:
        outcome = await run_trial_notifier(session, email_provider=ctx.get("email"))
    logger.info("trial_notifier %s", outcome)
    return str(outcome)


async def trial_enforcer(ctx: dict[str, Any]) -> str:
    with ctx["session_factory"]() as session:
        outcome = await run_trial_enforcer(session, email_provider=ctx.get("email"))
    logger.info("trial_enforcer %s", outcome)
    return str(outcome)


async def tenant_archiver(ctx: dict[str, Any]) -> str:
    with ctx["session_factory"]() as session:
        outcome = await run_tenant_archiver(session)
    logger.info("tenant_archiver %s", outcome)
    return str(outcome)


async def wizard_reminder(ctx: dict[str, Any]) -> str:
    with ctx["session_factory"]() as session:
        outcome = await run_wizard_reminder(session, email_provider=ctx.get("email"))
    logger.info("wizard_reminder %s", outcome)
    return str(outcome)
