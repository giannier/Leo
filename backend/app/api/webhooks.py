"""Provider webhooks. Public, signed, and outside `/api/v1`.

Not under the versioned prefix on purpose: the URL is configured in somebody
else's console, and a version bump that moved it would silently stop a
customer's WhatsApp number from working until a human noticed.

The endpoint does three things and nothing else - verify, store, queue -
because RF-001 gives it two seconds and because everything it *could* do here
is better done in a worker against a row that has already been written. A
parser that gets a field wrong can then be fixed and the event replayed; a
parser that ran inside this request would have thrown the message away.

What never happens here is a 500. This URL is reachable by anyone, so every
failure is an explicit status: 401 for a signature that does not check out,
400 for a body that is not a usable event, 503 when the queue is unreachable
and the provider should redeliver.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel

from app.api.deps import JobQueueDep, SessionDep, WhatsAppProviderDep
from app.api.errors import ApiError
from app.core.config import get_settings
from app.services.channel import WebhookIntake
from app.workers.channel import PROCESS_EVENT_JOB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookAck(BaseModel):
    """What the provider is told. Deliberately says nothing about the tenant.

    The endpoint is public: a response that distinguished "number I know" from
    "number I do not" would let anybody enumerate which businesses are on the
    platform by sending one webhook per phone number.
    """

    received: bool


@router.post(
    "/ycloud",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Receive one YCloud delivery",
    include_in_schema=False,
)
async def receive_ycloud(
    request: Request,
    response: Response,
    session: SessionDep,
    provider: WhatsAppProviderDep,
    queue: JobQueueDep,
    ycloud_signature: str | None = Header(default=None, alias="YCloud-Signature"),
) -> WebhookAck:
    """Accept a delivery, store it, and hand it to a worker."""
    # The raw bytes, before any parsing. The signature is over exactly these:
    # re-serialising the parsed object changes key order and whitespace, and a
    # check written against that rejects every genuine delivery - which then
    # gets "fixed" by deleting the check.
    raw_body = await request.body()

    intake = WebhookIntake(session, provider=provider, secret=get_settings().ycloud_webhook_secret)
    if not intake.is_signed(raw_body=raw_body, header=ycloud_signature):
        # Logged at warning because it is either a misconfiguration or
        # somebody probing, and both deserve to be visible (HU-01).
        logger.warning(
            "refused an unsigned webhook delivery",
            extra={"had_header": ycloud_signature is not None, "bytes": len(raw_body)},
        )
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error="invalid_signature",
            message="The delivery is not signed by the provider.",
        )

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="invalid_payload",
            message="The body is not JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="invalid_payload",
            message="The body is not a webhook event.",
        )

    stored = intake.store(payload)
    if stored is None:
        # No event id means no idempotency. Storing it under one we invented
        # would make the provider's own retry arrive as a second message.
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="invalid_payload",
            message="The event carries no id.",
        )
    event_id, needs_processing = stored
    # Committed before the enqueue, so the row exists no matter what the queue
    # does next. The reverse order has a worker reading an event that its own
    # transaction has not written yet.
    session.commit()

    if not needs_processing:
        return WebhookAck(received=True)

    try:
        await queue.enqueue(PROCESS_EVENT_JOB, str(event_id))
    except Exception:
        # The delivery is stored, so nothing is lost - but nothing will pick
        # it up either until somebody retries. Answering 503 asks the provider
        # to redeliver, and the redelivery finds the stored row still unfinished
        # and queues it again.
        logger.exception("could not queue a stored webhook event")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return WebhookAck(received=False)

    return WebhookAck(received=True)
