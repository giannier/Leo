"""Platform admin endpoints (TASK-018).

The `/platform-admin` section of `contracts/api.md`. Gated by
`is_platform_admin`, read from the database on every request.

This is the only router in the application that is not tenant-scoped, and it
is the reason `record_audit` exists: an operator here can read any business's
figures, connect a number on its behalf and extend its trial. The tenant
cannot see those actions, so the audit log is the only place they are visible
at all (RF-012).

Credentials go **in** through this router and never come back out. There is no
endpoint that returns `credentials_enc` decrypted, deliberately: an admin who
needs to change them re-enters them, and an admin who wants to read them has a
question the audit log should be asked instead.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import EmailProviderDep, PlatformAdminDep, SessionDep, WhatsAppProviderDep
from app.api.errors import ApiError, error_responses
from app.core.config import get_settings
from app.providers.email.base import EmailProvider
from app.providers.whatsapp.base import NumberCredentials
from app.services.admin import (
    CredentialsRequired,
    IllegalTransition,
    NumberAlreadyConnected,
    PlatformAdminService,
    RejectionReasonRequired,
    RequestNotFound,
    TenantNotFound,
)
from app.services.emails import build_number_connected_email, build_number_rejected_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform-admin", tags=["platform-admin"])

# Spelled out rather than derived from the model enums because `Literal` needs
# literal members to type-check. `tests/unit/test_admin_contract.py` fails if
# they ever drift apart, so the duplication cannot rot silently.
RequestStatus = Literal["requested", "in_progress", "connected", "rejected"]
TenantStatus = Literal["trial", "active", "paused", "archived"]

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=512)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

# Extending a trial is a commercial concession, not an unlimited grant. The
# ceiling is here rather than in the service because it is a product decision
# about what one click may give away, and a typo of 3650 should be a 400 and
# not a decade of free service.
MAX_EXTENSION_DAYS = 90

ADMIN_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (404, "`not_found` - including for a caller who is not a platform admin"),
)


class CredentialsPayload(BaseModel):
    """What the admin pastes in after doing the provider work by hand."""

    provider: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = "ycloud"
    values: dict[str, str]


class TransitionPayload(BaseModel):
    status: RequestStatus
    credentials: CredentialsPayload | None = None
    rejection_reason: Reason | None = None
    admin_notes: Notes | None = None


class ExtendTrialPayload(BaseModel):
    days: Annotated[int, Field(ge=1, le=MAX_EXTENSION_DAYS)]
    reason: Reason


class ConnectionRequestAdminResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    phone_e164: str
    display_name: str
    business_description: str | None
    status: str
    rejection_reason: str | None
    admin_notes: str | None
    previous_request_id: uuid.UUID | None
    processed_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class TenantSummaryResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    trial_ends_at: datetime | None
    created_at: datetime


class TenantDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    trial_ends_at: datetime | None
    paused_at: datetime | None
    created_at: datetime
    owner_email: str | None
    members: int
    products: int
    knowledge_chars: int
    has_number: bool


def _not_found(what: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND, error="not_found", message=f"{what} not found."
    )


async def _notify(
    service: PlatformAdminService,
    email_provider: EmailProvider | None,
    *,
    tenant_id: uuid.UUID,
    phone_e164: str,
    connected: bool,
    reason: str | None,
) -> None:
    """Tell the owner what happened to their request.

    Called **after** the transition has committed, and deliberately unable to
    undo it: the number is live either way, and a mail outage that rolled back
    a completed connection would leave the customer with a working number the
    platform believes it never connected.

    Rejections are notified too. The task text only names the connected case,
    but a customer who is not told is a customer who waits indefinitely for an
    answer that already exists - and the reason is the only thing that lets
    them correct and re-file.
    """
    if email_provider is None:
        return
    target = service.notification_target(tenant_id)
    if target is None:
        return

    panel_url = f"{get_settings().frontend_base_url.rstrip('/')}/canal"
    message = (
        build_number_connected_email(
            to=target.email,
            tenant_name=target.tenant_name,
            phone_e164=phone_e164,
            panel_url=panel_url,
        )
        if connected
        else build_number_rejected_email(
            to=target.email,
            tenant_name=target.tenant_name,
            phone_e164=phone_e164,
            reason=reason or "No se indico un motivo.",
            panel_url=panel_url,
        )
    )
    await email_provider.send(message)


@router.get(
    "/connection-requests",
    summary="The connection queue",
    responses=error_responses(*ADMIN_RESPONSES),
)
async def list_requests(
    admin: PlatformAdminDep,
    session: SessionDep,
    request_status: Annotated[RequestStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ConnectionRequestAdminResponse]:
    """Oldest first. It is a work queue, not a feed."""
    service = PlatformAdminService(session, actor=admin)
    return [
        ConnectionRequestAdminResponse.model_validate(row, from_attributes=True)
        for row in service.queue(status=request_status, limit=limit)
    ]


@router.put(
    "/connection-requests/{request_id}",
    summary="Move a connection request forward",
    responses=error_responses(
        (
            400,
            "`validation_error`, `illegal_transition`, `credentials_required` "
            "or `rejection_reason_required`",
        ),
        (409, "`phone_already_connected`"),
        *ADMIN_RESPONSES,
    ),
)
async def transition_request(
    request_id: uuid.UUID,
    payload: TransitionPayload,
    admin: PlatformAdminDep,
    session: SessionDep,
    whatsapp: WhatsAppProviderDep,
    email_provider: EmailProviderDep,
) -> ConnectionRequestAdminResponse:
    """`connected` creates the number with encrypted credentials; `rejected`
    requires a reason the customer can act on. Both are audited."""
    service = PlatformAdminService(session, actor=admin)
    credentials = (
        NumberCredentials(provider=payload.credentials.provider, values=payload.credentials.values)
        if payload.credentials
        else None
    )
    try:
        updated = service.transition(
            request_id,
            status=payload.status,
            credentials=credentials,
            rejection_reason=payload.rejection_reason,
            admin_notes=payload.admin_notes,
            provider=whatsapp,
        )
    except RequestNotFound as exc:
        raise _not_found("Connection request") from exc
    except IllegalTransition as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="illegal_transition",
            message=str(exc),
        ) from exc
    except CredentialsRequired as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="credentials_required",
            message=str(exc),
        ) from exc
    except RejectionReasonRequired as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="rejection_reason_required",
            message="Give a reason the customer can act on.",
        ) from exc
    except NumberAlreadyConnected as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="phone_already_connected",
            message="That number was connected by another request while this one was open.",
        ) from exc

    if payload.status in ("connected", "rejected"):
        try:
            await _notify(
                service,
                email_provider,
                tenant_id=updated.tenant_id,
                phone_e164=updated.phone_e164,
                connected=payload.status == "connected",
                reason=payload.rejection_reason,
            )
        except Exception:
            # The transition is committed and audited; the number is live.
            # Reporting a 500 here would tell the admin their action failed
            # when it did not, and the natural response - retrying - hits
            # `illegal_transition` and looks like a second, different bug.
            # The customer can still see the outcome in their panel.
            logger.exception(
                "notification failed after %s (request %s); the transition stands",
                payload.status,
                updated.id,
            )

    return ConnectionRequestAdminResponse.model_validate(updated, from_attributes=True)


@router.get("/tenants", summary="Every business", responses=error_responses(*ADMIN_RESPONSES))
async def list_tenants(
    admin: PlatformAdminDep,
    session: SessionDep,
    search: Annotated[str | None, Query(max_length=120)] = None,
    tenant_status: Annotated[TenantStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TenantSummaryResponse]:
    service = PlatformAdminService(session, actor=admin)
    return [
        TenantSummaryResponse.model_validate(row, from_attributes=True)
        for row in service.list_tenants(search=search, status=tenant_status, limit=limit)
    ]


@router.get(
    "/tenants/{tenant_id}",
    summary="One business, with the figures an operator needs",
    responses=error_responses(*ADMIN_RESPONSES),
)
async def get_tenant(
    tenant_id: uuid.UUID, admin: PlatformAdminDep, session: SessionDep
) -> TenantDetailResponse:
    service = PlatformAdminService(session, actor=admin)
    try:
        usage = service.tenant_usage(tenant_id)
    except TenantNotFound as exc:
        raise _not_found("Tenant") from exc

    return TenantDetailResponse(
        id=usage.tenant.id,
        name=usage.tenant.name,
        status=usage.tenant.status,
        trial_ends_at=usage.tenant.trial_ends_at,
        paused_at=usage.tenant.paused_at,
        created_at=usage.tenant.created_at,
        owner_email=usage.owner_email,
        members=usage.members,
        products=usage.products,
        knowledge_chars=usage.knowledge_chars,
        has_number=usage.has_number,
    )


@router.post(
    "/tenants/{tenant_id}/extend-trial",
    summary="Give a trial more time",
    responses=error_responses((400, "`validation_error`"), *ADMIN_RESPONSES),
)
async def extend_trial(
    tenant_id: uuid.UUID,
    payload: ExtendTrialPayload,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> TenantDetailResponse:
    """Audited with the reason, because a trial extended without one is
    indistinguishable from an account quietly kept free."""
    service = PlatformAdminService(session, actor=admin)
    try:
        service.extend_trial(tenant_id, days=payload.days, reason=payload.reason)
        usage = service.tenant_usage(tenant_id)
    except TenantNotFound as exc:
        raise _not_found("Tenant") from exc

    return TenantDetailResponse(
        id=usage.tenant.id,
        name=usage.tenant.name,
        status=usage.tenant.status,
        trial_ends_at=usage.tenant.trial_ends_at,
        paused_at=usage.tenant.paused_at,
        created_at=usage.tenant.created_at,
        owner_email=usage.owner_email,
        members=usage.members,
        products=usage.products,
        knowledge_chars=usage.knowledge_chars,
        has_number=usage.has_number,
    )
