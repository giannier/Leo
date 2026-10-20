"""Connection request endpoints (TASK-017).

The `/tenant/connection-requests` and `/tenant/whatsapp-number` sections of
`contracts/api.md`.

v1 connects numbers manually: this files the request, and TASK-018 gives the
admin the queue. The customer sees a status they can follow rather than a form
that appears to do nothing.

Credentials never appear in a response here. `whatsapp_numbers.credentials_enc`
is readable only by the admin path that writes it - a tenant has no use for
its own provider tokens, and returning them would put them in browser history,
logs and screenshots for no gain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, StringConstraints

from app.api.deps import ConfigurationManagerDep, MemberDep, SessionDep, WhatsAppProviderDep
from app.api.errors import ApiError, error_responses
from app.providers.whatsapp.base import (
    InvalidCredentials,
    NumberNotInAccount,
    WhatsAppAuthError,
    WhatsAppError,
)
from app.services.connection import (
    ConnectionService,
    OpenRequestExists,
    PhoneAlreadyConnected,
    PhoneNotVerified,
    WebhookRegistrationFailed,
)

router = APIRouter(prefix="/tenant", tags=["connection"])

E164_PATTERN = r"^\+[1-9]\d{7,14}$"
PhoneE164 = Annotated[str, StringConstraints(strip_whitespace=True, pattern=E164_PATTERN)]
# WhatsApp caps the display name at 25 characters; accepting more here would
# mean a request the admin cannot actually fulfil.
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=25)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


class ConnectionRequestPayload(BaseModel):
    phone_e164: PhoneE164
    display_name: DisplayName
    business_description: Description | None = None
    profile_photo_key: Annotated[str, StringConstraints(max_length=512)] | None = None
    # Set when re-filing after a rejection, so the admin can see the history
    # of what was corrected (Clarification 012).
    previous_request_id: uuid.UUID | None = None


class ConnectNumberPayload(BaseModel):
    """The business's own YCloud API key and the number it wants to connect."""

    api_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=200)]
    phone_e164: PhoneE164
    display_name: DisplayName


class ConnectionRequestResponse(BaseModel):
    id: uuid.UUID
    phone_e164: str
    display_name: str
    # Returned so a rejected request can be corrected without retyping it.
    # Clarification 012 makes re-filing the expected path, and a retry form
    # that arrives empty is one the customer abandons.
    business_description: str | None
    status: str
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class WhatsAppNumberResponse(BaseModel):
    id: uuid.UUID
    phone_e164: str
    display_name: str
    provider: str
    status: str
    quality_rating: str | None
    connected_at: datetime


@router.post(
    "/connection-requests",
    status_code=status.HTTP_201_CREATED,
    summary="Ask us to connect a WhatsApp number",
    responses=error_responses(
        (400, "`validation_error`"),
        (403, "`phone_not_verified` or `insufficient_role`"),
        (409, "`phone_already_connected` or `open_request_exists`"),
        *AUTH_RESPONSES,
    ),
)
async def create_request(
    payload: ConnectionRequestPayload,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> ConnectionRequestResponse:
    """Files the request. A person does the provider work (Clarification 007)."""
    service = ConnectionService(session, principal.tenant_id)
    try:
        request = service.request(
            user=principal.user,
            phone_e164=payload.phone_e164,
            display_name=payload.display_name,
            business_description=payload.business_description,
            profile_photo_key=payload.profile_photo_key,
            previous_request_id=payload.previous_request_id,
        )
    except PhoneNotVerified as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="phone_not_verified",
            message="Verify your contact phone before requesting a number.",
        ) from exc
    except PhoneAlreadyConnected as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="phone_already_connected",
            message="That number is already connected. Contact support if it is yours.",
        ) from exc
    except OpenRequestExists as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="open_request_exists",
            message="There is already an open request for that number.",
        ) from exc

    return ConnectionRequestResponse.model_validate(request, from_attributes=True)


@router.get(
    "/connection-requests",
    summary="Your connection requests and their status",
    responses=error_responses((403, "`insufficient_role`"), *AUTH_RESPONSES),
)
async def list_requests(
    principal: ConfigurationManagerDep, session: SessionDep
) -> list[ConnectionRequestResponse]:
    """History, not just the open one: a rejected request and its reason are
    what tell the customer what to fix before filing again."""
    service = ConnectionService(session, principal.tenant_id)
    return [
        ConnectionRequestResponse.model_validate(request, from_attributes=True)
        for request in service.list_requests()
    ]


@router.get(
    "/whatsapp-number",
    summary="The connected number, if there is one",
    responses=error_responses((404, "`no_number`"), *AUTH_RESPONSES),
)
async def get_number(principal: MemberDep, session: SessionDep) -> WhatsAppNumberResponse:
    """Any member may see it: the number is the business's public identity,
    not a secret. Its credentials are never included."""
    number = ConnectionService(session, principal.tenant_id).connected_number()
    if number is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            error="no_number",
            message="No WhatsApp number is connected yet.",
        )
    return WhatsAppNumberResponse.model_validate(number, from_attributes=True)


@router.post(
    "/whatsapp-number",
    status_code=status.HTTP_201_CREATED,
    summary="Connect your WhatsApp number with your own YCloud account",
    responses=error_responses(
        (400, "`invalid_api_key`, `number_not_in_account` or `webhook_not_created`"),
        (403, "`insufficient_role`"),
        (409, "`phone_already_connected`"),
        (502, "`provider_unavailable`"),
        *AUTH_RESPONSES,
    ),
)
async def connect_number(
    payload: ConnectNumberPayload,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    provider: WhatsAppProviderDep,
) -> WhatsAppNumberResponse:
    """Checks the key against YCloud, creates the webhook in the business's account
    and stores the credentials encrypted. Nothing about the key is echoed back."""
    service = ConnectionService(session, principal.tenant_id)
    try:
        number = await service.connect_ycloud(
            user=principal.user,
            api_key=payload.api_key,
            phone_e164=payload.phone_e164,
            display_name=payload.display_name,
            provider=provider,
        )
    except PhoneAlreadyConnected as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="phone_already_connected",
            message="That number is already connected. Contact support if it is yours.",
        ) from exc
    except (WhatsAppAuthError, InvalidCredentials) as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="invalid_api_key",
            message="YCloud did not accept that API key. Copy it again from Developers > API key.",
        ) from exc
    except NumberNotInAccount as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="number_not_in_account",
            message="That number is not in the YCloud account this key belongs to.",
        ) from exc
    except WebhookRegistrationFailed as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="webhook_not_created",
            message=(
                "YCloud would not let us create the webhook. "
                "Check that the key has access to webhooks."
            ),
        ) from exc
    except WhatsAppError as exc:
        raise ApiError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            error="provider_unavailable",
            message="We could not reach YCloud. Try again in a minute.",
        ) from exc
    return WhatsAppNumberResponse.model_validate(number, from_attributes=True)
