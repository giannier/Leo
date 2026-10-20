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
from sqlalchemy.orm import Session

from app.api.deps import JobQueueDep, SessionDep, WhatsAppProviderDep
from app.api.errors import ApiError
from app.repositories.whatsapp import number_by_webhook_token
from app.services.channel import WebhookIntake
from app.services.connection import read_credentials
from app.workers.channel import PROCESS_EVENT_JOB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _secret_for(session: Session, token: str) -> str | None:
    """The signing secret of the number this URL belongs to, if there is one."""
    number = number_by_webhook_token(session, token)
    if number is None or number.status != "connected":
        return None
    credentials = read_credentials(number)
    if credentials is None:
        return None
    return credentials.values.get("webhook_secret") or None


class WebhookAck(BaseModel):
    """What the provider is told. Deliberately says nothing about the tenant.

    The endpoint is public: a response that distinguished "number I know" from
    "number I do not" would let anybody enumerate which businesses are on the
    platform by sending one webhook per phone number.
    """

    received: bool


@router.post(
    "/ycloud/{token}",
    response_model=WebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Receive one YCloud delivery for one number",
    include_in_schema=False,
)
async def receive_ycloud(
    token: str,
    request: Request,
    response: Response,
    session: SessionDep,
    provider: WhatsAppProviderDep,
    queue: JobQueueDep,
    ycloud_signature: str | None = Header(default=None, alias="YCloud-Signature"),
) -> WebhookAck:
    """Accept a delivery, store it, and hand it to a worker.

    Every business has its own YCloud account and so its own signing secret. The
    token in the URL says whose; the signature is then checked against that
    number's secret, never a platform-wide one.
    """
    # The raw bytes, before any parsing. The signature is over exactly these:
    # re-serialising the parsed object changes key order and whitespace, and a
    # check written against that rejects every genuine delivery - which then
    # gets "fixed" by deleting the check.
    raw_body = await request.body()

    intake = WebhookIntake(session, provider=provider, secret=_secret_for(session, token))
    if not intake.is_signed(raw_body=raw_body, header=ycloud_signature):
        # An unknown token and a bad signature answer the same, so the response
        # cannot be used to find out which tokens exist.
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
