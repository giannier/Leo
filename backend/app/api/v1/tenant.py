"""Tenant state, activation checklist, payment methods and the agent switch.

The `/tenant`, `/tenant/activation-checklist`, `/tenant/payment-methods` and
`/tenant/agent/*` sections of `contracts/api.md`.

Activation goes through the same `build_checklist` the customer polls, so the
wizard and the button can never disagree about what "ready" means - the
failure that shows a green checklist next to a control that refuses to work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.exc import IntegrityError

from app.api.deps import ConfigurationManagerDep, MemberDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.repositories.business import AgentConfigRepository
from app.repositories.conversation import MessageRepository
from app.repositories.payment import PaymentMethodRepository
from app.repositories.plan import PlanRepository
from app.repositories.tenant import TenantRepository
from app.services.activation import build_checklist
from app.services.trial import billing_enforced

router = APIRouter(prefix="/tenant", tags=["tenant"])

PaymentMethodName = Literal["yape", "plin", "transferencia", "tarjeta", "efectivo", "link"]
Instructions = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


class TrialStatus(BaseModel):
    ends_at: datetime | None
    ai_messages_used: int
    ai_messages_limit: int
    days_left: int | None


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    plan: str
    trial: TrialStatus | None
    # False while the platform does not charge (`BILLING_ENFORCED` off): the
    # trial never ends and the limits are not enforced.
    cobro_activo: bool


class UsageResponse(BaseModel):
    """Live consumption, for the banner that watches a busy afternoon."""

    ai_messages_used: int
    ai_messages_limit: int
    percent_used: int
    messages_in: int
    messages_out: int
    period_start: datetime
    # False while the platform does not charge: the limit is shown, not enforced.
    cobro_activo: bool


class ChecklistItemResponse(BaseModel):
    key: str
    done: bool
    required: bool


class ChecklistResponse(BaseModel):
    items: list[ChecklistItemResponse]
    sandbox_ready: bool
    activation_ready: bool


class PaymentMethodRequest(BaseModel):
    method: PaymentMethodName
    instructions: Instructions
    # Turns on the receipt-vision tool that arrives in Feature 004.
    validate_receipts: bool = False
    is_active: bool = True


class PaymentMethodPatch(BaseModel):
    instructions: Instructions | None = None
    validate_receipts: bool | None = None
    is_active: bool | None = None


class PaymentMethodResponse(BaseModel):
    id: uuid.UUID
    method: str
    instructions: str
    validate_receipts: bool
    is_active: bool


def _not_found() -> ApiError:
    return ApiError(status_code=status.HTTP_404_NOT_FOUND, error="not_found", message="Not found.")


@router.get("", summary="Tenant state and trial usage", responses=error_responses(*AUTH_RESPONSES))
async def get_tenant(principal: MemberDep, session: SessionDep) -> TenantResponse:
    tenant = TenantRepository(session).get(principal.tenant_id)
    if tenant is None:  # pragma: no cover - the caller is a member of it
        raise _not_found()
    plan = PlanRepository(session).get(tenant.plan_id)

    trial: TrialStatus | None = None
    if tenant.status == "trial" and plan is not None:
        days_left: int | None = None
        if tenant.trial_ends_at is not None:
            remaining = tenant.trial_ends_at - datetime.now(UTC)
            # Floored at zero rather than allowed negative: an expired trial
            # reads "0 days left", and the tenant's own status is what says it
            # ended.
            days_left = max(remaining.days, 0)
        trial = TrialStatus(
            ends_at=tenant.trial_ends_at,
            ai_messages_used=tenant.ai_messages_used,
            ai_messages_limit=plan.max_ai_messages,
            days_left=days_left,
        )

    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        status=tenant.status,
        plan=tenant.plan_id,
        trial=trial,
        cobro_activo=billing_enforced(),
    )


@router.get(
    "/usage",
    summary="Live consumption on the real channel",
    responses=error_responses(*AUTH_RESPONSES),
)
async def get_usage(principal: MemberDep, session: SessionDep) -> UsageResponse:
    """What the trial has spent, right now (HU-04).

    Separate from `GET /tenant` even though the numbers overlap, because the
    two are read at different rates. The panel loads tenant state once; the
    usage banner polls while somebody watches their agent work through a busy
    afternoon, and that read has no business dragging the plan, the
    checklist and the trial dates along with it.

    Counted from `messages` rather than from a stored total: `ai_messages_used`
    is the trial's meter and resets with nothing, while the owner asking "how
    busy were we?" means the real traffic in both directions.
    """
    tenant = TenantRepository(session).get(principal.tenant_id)
    if tenant is None:  # pragma: no cover - the caller is a member of it
        raise _not_found()
    plan = PlanRepository(session).get(tenant.plan_id)
    limit = plan.max_ai_messages if plan else 0

    since = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    messages = MessageRepository(session, principal.tenant_id)

    return UsageResponse(
        ai_messages_used=tenant.ai_messages_used,
        ai_messages_limit=limit,
        cobro_activo=billing_enforced(),
        # Capped at 100: an admin lowering a plan's limit must not produce a
        # progress bar past the end of its own track.
        percent_used=min(100, round(100 * tenant.ai_messages_used / limit)) if limit else 0,
        messages_in=messages.count_on_channel("whatsapp", direction="inbound", since=since),
        messages_out=messages.count_on_channel("whatsapp", direction="outbound", since=since),
        period_start=since,
    )


@router.get(
    "/activation-checklist",
    summary="What is still missing before the agent can go live",
    responses=error_responses((403, "`insufficient_role`"), *AUTH_RESPONSES),
)
async def get_checklist(
    principal: ConfigurationManagerDep, session: SessionDep
) -> ChecklistResponse:
    """Computed live from the entities, never stored (Clarification 009)."""
    checklist = build_checklist(session, tenant_id=principal.tenant_id, user=principal.user)
    return ChecklistResponse(
        items=[
            ChecklistItemResponse(key=item.key, done=item.done, required=item.required)
            for item in checklist.items
        ],
        sandbox_ready=checklist.sandbox_ready,
        activation_ready=checklist.activation_ready,
    )


@router.get(
    "/payment-methods",
    summary="How this business gets paid",
    responses=error_responses(*AUTH_RESPONSES),
)
async def list_payment_methods(
    principal: MemberDep, session: SessionDep
) -> list[PaymentMethodResponse]:
    repository = PaymentMethodRepository(session, principal.tenant_id)
    return [
        PaymentMethodResponse.model_validate(method, from_attributes=True)
        for method in repository.list()
    ]


@router.post(
    "/payment-methods",
    status_code=status.HTTP_201_CREATED,
    summary="Add a payment method",
    responses=error_responses(
        (400, "`validation_error`"),
        (409, "`method_already_exists`"),
        (403, "`insufficient_role`"),
        *AUTH_RESPONSES,
    ),
)
async def create_payment_method(
    payload: PaymentMethodRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> PaymentMethodResponse:
    """One row per method. A business has one set of Yape instructions, not
    several, so the unique constraint is the point rather than a limitation."""
    repository = PaymentMethodRepository(session, principal.tenant_id)
    try:
        method = repository.add(**payload.model_dump())
        session.commit()
    except IntegrityError as exc:
        # The database is the arbiter, not a prior SELECT: checking first and
        # inserting after races two concurrent adds of the same method.
        session.rollback()
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="method_already_exists",
            message=f"{payload.method} is already configured. Edit it instead.",
        ) from exc
    return PaymentMethodResponse.model_validate(method, from_attributes=True)


@router.put(
    "/payment-methods/{method_id}",
    summary="Update a payment method",
    responses=error_responses(
        (400, "`validation_error`"),
        (404, "`not_found`"),
        (403, "`insufficient_role`"),
        *AUTH_RESPONSES,
    ),
)
async def update_payment_method(
    method_id: uuid.UUID,
    payload: PaymentMethodPatch,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> PaymentMethodResponse:
    """`method` itself is not editable: changing Yape into Plin would silently
    rewrite instructions customers were already given. Delete and add."""
    repository = PaymentMethodRepository(session, principal.tenant_id)
    updated = repository.update(method_id, **payload.model_dump(exclude_unset=True))
    if updated is None:
        raise _not_found()
    session.commit()
    return PaymentMethodResponse.model_validate(updated, from_attributes=True)


@router.delete(
    "/payment-methods/{method_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a payment method",
    responses=error_responses((404, "`not_found`"), (403, "`insufficient_role`"), *AUTH_RESPONSES),
)
async def delete_payment_method(
    method_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> None:
    repository = PaymentMethodRepository(session, principal.tenant_id)
    if not repository.delete(method_id):
        raise _not_found()
    session.commit()


@router.post(
    "/agent/activate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Put the agent in front of real customers",
    responses=error_responses(
        (409, "`checklist_incomplete` or `number_not_connected`"),
        (403, "`insufficient_role`"),
        *AUTH_RESPONSES,
    ),
)
async def activate_agent(principal: ConfigurationManagerDep, session: SessionDep) -> None:
    """Guarded by the same checklist the customer sees.

    Sharing the function is the point: a second, separately written guard is
    how a wizard ends up showing all green next to a button that refuses.
    """
    checklist = build_checklist(session, tenant_id=principal.tenant_id, user=principal.user)
    if checklist.blocker is not None:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error=checklist.blocker,
            message=(
                "Finish the activation checklist first."
                if checklist.blocker == "checklist_incomplete"
                else "Your WhatsApp number is not connected yet."
            ),
        )

    repository = AgentConfigRepository(session, principal.tenant_id)
    repository.set_active(is_active=True, activated_at=datetime.now(UTC))
    session.commit()


@router.post(
    "/agent/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop the agent answering",
    responses=error_responses((403, "`insufficient_role`"), *AUTH_RESPONSES),
)
async def deactivate_agent(principal: ConfigurationManagerDep, session: SessionDep) -> None:
    """Unguarded on purpose. Turning the agent off is how a customer stops it
    saying something wrong, and that must never be blocked by a checklist."""
    AgentConfigRepository(session, principal.tenant_id).set_active(is_active=False)
    session.commit()
