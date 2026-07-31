"""Turning the agent on and off (TASK-019).

The `/tenant/agent/activate` and `/tenant/agent/deactivate` endpoints of
`contracts/api.md`.

Activation is the moment the business goes live: from here the agent answers
real customers on a real number, in the business's name. So it is guarded by
the checklist rather than by a confirmation dialog - the failure being avoided
is an agent that greets a paying customer and then cannot say what the
business sells or how to pay for it.

Deactivation has no guards at all, deliberately. It is the stop button, and a
stop button that asks questions is not one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api.deps import ConfigurationManagerDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.repositories.business import AgentConfigRepository
from app.repositories.tenant import TenantRepository
from app.services.activation import build_checklist, owner_of

router = APIRouter(prefix="/tenant/agent", tags=["agent"])

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (
        403,
        "`email_not_verified`, `no_active_tenant`, `tenant_access_denied` or `insufficient_role`",
    ),
)

# States in which the agent may be switched on. A `paused` tenant is one whose
# trial ended: the agent is stopped by that alone, so accepting the request
# would leave the panel showing "activo" for an agent that answers nobody.
# `archived` is further still.
ACTIVATABLE_STATUSES = ("trial", "active")

BLOCKER_MESSAGES = {
    "checklist_incomplete": "Finish the onboarding checklist before activating the agent.",
    "number_not_connected": "Connect a WhatsApp number before activating the agent.",
}


@router.post(
    "/activate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Let the agent answer real customers",
    responses=error_responses(
        (409, "`checklist_incomplete`, `number_not_connected` or `tenant_not_active`"),
        *AUTH_RESPONSES,
    ),
)
async def activate(principal: ConfigurationManagerDep, session: SessionDep) -> None:
    """Guarded by the checklist, which is computed rather than stored."""
    tenant = TenantRepository(session).get(principal.tenant_id)
    if tenant is None or tenant.status not in ACTIVATABLE_STATUSES:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="tenant_not_active",
            message="Your trial has ended. Choose a plan to put the agent back to work.",
        )

    # Evaluated against the owner, not the caller: items 1 and 6 are about a
    # person, and a tenant_admin activating should not be blocked by the
    # owner's unverified phone being read as their own - nor pass because
    # theirs happens to be verified.
    owner = owner_of(session, principal.tenant_id) or principal.user
    checklist = build_checklist(session, tenant_id=principal.tenant_id, user=owner)
    if not checklist.activation_ready:
        blocker = checklist.blocker or "checklist_incomplete"
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error=blocker,
            message=BLOCKER_MESSAGES[blocker],
        )

    # `activated_first_at` is stamped once and never again - see `set_active`.
    # It answers "how long from registration to a working agent?", one of this
    # feature's success metrics, and a value that moved on every
    # re-activation would answer nothing.
    AgentConfigRepository(session, principal.tenant_id).set_active(
        is_active=True, activated_at=datetime.now(UTC)
    )
    session.commit()


@router.post(
    "/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop the agent answering",
    responses=error_responses(*AUTH_RESPONSES),
)
async def deactivate(principal: ConfigurationManagerDep, session: SessionDep) -> None:
    """No guards, and idempotent.

    This is the button somebody presses because the agent is saying something
    wrong to real customers right now. It must work on the first press, from
    any state, without explaining itself.
    """
    configs = AgentConfigRepository(session, principal.tenant_id)
    config = configs.current()
    if config is not None and config.is_active:
        configs.set_active(is_active=False)
        session.commit()
