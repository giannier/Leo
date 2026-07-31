"""Team endpoints: members, roles and invitations.

Implements the `/tenant/team` section of
`leo-sdd/features/001-onboarding/contracts/api.md`.

Authorisation follows Clarification 008: `owner` and `tenant_admin` manage the
team, `operator` cannot. Demoting or disabling an owner is owner-only, because
otherwise an admin could remove the person who holds billing and account
deletion.

Acceptance is not here - it is a public endpoint on `/auth`, since the person
accepting has no session yet and, for a brand-new user, no account either.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, StringConstraints, model_validator

from app.api.deps import (
    AccountOwnerDep,
    ConfigurationManagerDep,
    EmailProviderDep,
    MemberDep,
    SessionDep,
)
from app.api.errors import ApiError, error_responses
from app.core.config import get_settings
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository
from app.services.emails import build_invitation_email
from app.services.team import (
    INVITATION_TTL_DAYS,
    AlreadyMember,
    CannotDemoteLastOwner,
    InvitationExpired,
    InvitationNotFound,
    MemberNotFound,
    SeatLimitReached,
    TeamService,
)

router = APIRouter(prefix="/tenant/team", tags=["team"])

# 'owner' is absent on purpose: ownership transfer is a role change on an
# existing member, never an invitation.
InvitableRole = Literal["tenant_admin", "operator"]

EMAIL_PATTERN = r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$"
EmailAddress = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=EMAIL_PATTERN),
]


class InviteRequest(BaseModel):
    email: EmailAddress
    role: InvitableRole


class UpdateMemberRequest(BaseModel):
    """Either field may be sent; at least one must be."""

    role: Literal["owner", "tenant_admin", "operator"] | None = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> UpdateMemberRequest:
        if self.role is None and self.status is None:
            raise ValueError("Send `role`, `status`, or both.")
        return self


class MemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str


class InvitationResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime


class SeatsResponse(BaseModel):
    used: int
    limit: int


class TeamResponse(BaseModel):
    members: list[MemberResponse]
    invitations: list[InvitationResponse]
    seats: SeatsResponse


@router.get(
    "",
    summary="List members, pending invitations and seat usage",
    responses=error_responses(
        (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
        (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
    ),
)
async def list_team(principal: MemberDep, session: SessionDep) -> TeamResponse:
    """Any member may see who else is on the team."""
    service = TeamService(session, principal.tenant_id)
    users = UserRepository(session)

    members: list[MemberResponse] = []
    for membership in service.list_members():
        user = users.get(membership.user_id)
        if user is None:  # pragma: no cover - FK guarantees it
            continue
        members.append(
            MemberResponse(
                id=membership.id,
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=membership.role,
                status=membership.status,
            )
        )

    usage = service.seat_usage()
    return TeamResponse(
        members=members,
        invitations=[
            InvitationResponse(
                id=invitation.id,
                email=invitation.email,
                role=invitation.role,
                expires_at=invitation.expires_at,
            )
            for invitation in service.list_pending_invitations()
        ],
        seats=SeatsResponse(used=usage.used, limit=usage.limit),
    )


@router.post(
    "/invitations",
    status_code=status.HTTP_201_CREATED,
    summary="Invite somebody to the team",
    responses=error_responses(
        (400, "`validation_error`"),
        (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
        (403, "`insufficient_role`"),
        (409, "`seat_limit_reached` or `already_member`"),
    ),
)
async def invite_member(
    payload: InviteRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    email_provider: EmailProviderDep,
) -> InvitationResponse:
    """Create an invitation and email the link.

    The seat check counts pending invitations, not just accepted members: a
    sent invitation is a seat already promised.
    """
    service = TeamService(session, principal.tenant_id)
    try:
        invitation, token = service.invite(
            email=payload.email, role=payload.role, invited_by=principal.user_id
        )
    except AlreadyMember as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="already_member",
            message=str(exc),
        ) from exc
    except SeatLimitReached as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="seat_limit_reached",
            message=str(exc),
        ) from exc

    await _deliver(session, principal, invitation.email, token, email_provider)

    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
    )


@router.post(
    "/invitations/{invitation_id}/resend",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Issue a fresh link for a pending invitation",
    responses=error_responses(
        (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
        (403, "`insufficient_role`"),
        (404, "`not_found`"),
        (410, "`invitation_expired`"),
    ),
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    email_provider: EmailProviderDep,
) -> None:
    """Replace the token and send again. The previous link stops working."""
    service = TeamService(session, principal.tenant_id)
    try:
        invitation, token = service.reissue(invitation_id)
    except InvitationNotFound as exc:
        raise _not_found() from exc
    except InvitationExpired as exc:
        raise ApiError(
            status_code=status.HTTP_410_GONE,
            error="invitation_expired",
            message=str(exc),
        ) from exc

    await _deliver(session, principal, invitation.email, token, email_provider)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invitation",
    responses=error_responses(
        (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
        (403, "`insufficient_role`"),
        (404, "`not_found`"),
    ),
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> None:
    service = TeamService(session, principal.tenant_id)
    try:
        service.revoke_invitation(invitation_id)
    except InvitationNotFound as exc:
        raise _not_found() from exc


@router.put(
    "/members/{membership_id}",
    summary="Change a member's role or status",
    responses=error_responses(
        (400, "`validation_error`"),
        (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
        (403, "`insufficient_role`"),
        (404, "`not_found`"),
        (409, "`last_owner`"),
    ),
)
async def update_member(
    membership_id: uuid.UUID,
    payload: UpdateMemberRequest,
    principal: AccountOwnerDep,
    session: SessionDep,
) -> MemberResponse:
    """Owner-only.

    An admin who could demote or disable the owner could take the account
    away from the person who holds billing and deletion, so this endpoint is
    narrower than the rest of the router (contract: "DELETE de owner: solo
    owner").

    Disabling revokes the member's refresh tokens. Their access token keeps
    its signature until it expires, but membership is re-read on every
    request, so it stops working on the next call rather than in fifteen
    minutes.
    """
    service = TeamService(session, principal.tenant_id)
    try:
        membership = service.update_member(membership_id, role=payload.role, status=payload.status)
    except MemberNotFound as exc:
        raise _not_found() from exc
    except CannotDemoteLastOwner as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="last_owner",
            message=str(exc),
        ) from exc

    user = UserRepository(session).get(membership.user_id)
    assert user is not None
    return MemberResponse(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        status=membership.status,
    )


def _not_found() -> ApiError:
    """404 for anything this tenant does not own.

    Never 403: telling a caller that an id exists but belongs to someone else
    confirms the id exists at all.
    """
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        error="not_found",
        message="Not found.",
    )


async def _deliver(
    session: SessionDep,
    principal: MemberDep,
    email: str,
    token: str,
    email_provider: EmailProviderDep,
) -> None:
    """Send the invitation. A mail outage does not undo the invitation.

    The row is already committed by the service, so a failure here leaves a
    valid invitation the owner can resend, rather than losing the seat
    reservation and the audit trail.
    """
    if email_provider is None:
        return
    tenant = TenantRepository(session).get(principal.tenant_id)
    settings = get_settings()
    accept_url = f"{settings.frontend_base_url.rstrip('/')}/invitations/accept?token={token}"
    message = build_invitation_email(
        to=email,
        tenant_name=tenant.name if tenant else "tu equipo",
        inviter_name=principal.user.full_name,
        accept_url=accept_url,
        expires_in_days=INVITATION_TTL_DAYS,
    )
    await email_provider.send(message)
