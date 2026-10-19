"""What the channel's webhooks mean for Feature 011.

Called by `ChannelEventProcessor` inside the event's transaction:

* an inbound tap on the promotions question records the answer (C-102);
* an inbound message within 72 hours of a campaign marks the recipient as replied (C-109);
* a delivery report moves the recipient along (sent → delivered → read);
* a number whose quality drops pauses its campaigns (C-002, not switchable);
* BAJA revokes the permission for promotions (C-103).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.campaigns import ATTRIBUTION_HOURS, Campaign
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.providers.whatsapp.base import InteractiveReply
from app.repositories.campaigns import RECIPIENT_RANK, CampaignRecipientRepository
from app.repositories.conversation import MessageRepository
from app.services.campaigns.optins import BUTTON_NO, BUTTON_YES, OptinService

logger = logging.getLogger(__name__)

# Meta's ratings at which campaigns stop: YELLOW is the warning, RED the last step before
# the number is limited.
STOP_QUALITY = ("YELLOW", "RED", "FLAGGED")


def answer_to_question(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    contact: Contact,
    conversation: Conversation,
    tap: InteractiveReply | None,
    message_id: uuid.UUID,
    at: datetime,
) -> Message | None:
    """The fixed answer to a tap on the promotions question, written `queued`, or None."""
    if tap is None or tap.id not in (BUTTON_YES, BUTTON_NO):
        return None
    text = OptinService(session, tenant_id).record_answer(
        contact, granted=tap.id == BUTTON_YES, message_id=message_id, at=at
    )
    return MessageRepository(session, tenant_id).append(
        conversation_id=conversation.id,
        direction="outbound",
        author="system",
        content=text,
        delivery_status="queued",
        payload={"motivo": "optin_respuesta"},
    )


def track_reply(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    contact: Contact,
    conversation: Conversation,
    at: datetime,
) -> None:
    recipients = CampaignRecipientRepository(session, tenant_id)
    recipient = recipients.awaiting_reply(contact.id, at - timedelta(hours=ATTRIBUTION_HOURS))
    if recipient is None:
        return
    recipient.status = "replied"
    recipient.replied_at = at
    if conversation.origin_campaign_id is None and recipient.conversation_id == conversation.id:
        conversation.origin_campaign_id = recipient.campaign_id
    session.flush()


def keeps_template_thread(
    session: Session, thread: Conversation, previous_inbound_at: datetime | None
) -> bool:
    """A thread that got a template after the customer last wrote stays open for the answer.

    Campaigns and follow-ups outside the window (C-109, C-110): the customer answers the
    template, and the assistant must see what it said.
    """
    statement = select(Message.id).where(
        Message.tenant_id == thread.tenant_id,
        Message.conversation_id == thread.id,
        Message.msg_type == "template",
        Message.direction == "outbound",
    )
    if previous_inbound_at is not None:
        statement = statement.where(Message.created_at > previous_inbound_at)
    return session.execute(statement.limit(1)).scalar_one_or_none() is not None


def track_delivery(session: Session, tenant_id: uuid.UUID, message: Message, status: str) -> None:
    if message.msg_type != "template":
        return
    recipient = CampaignRecipientRepository(session, tenant_id).by_message(message.id)
    if recipient is None:
        return
    if RECIPIENT_RANK.get(status, 0) > RECIPIENT_RANK.get(recipient.status, 0):
        recipient.status = status
        session.flush()


def pause_for_quality(session: Session, tenant_id: uuid.UUID, rating: str) -> int:
    if rating.upper() not in STOP_QUALITY:
        return 0
    result = session.execute(
        update(Campaign)
        .where(Campaign.tenant_id == tenant_id, Campaign.status.in_(("scheduled", "sending")))
        .values(status="paused", pause_reason="calidad")
    )
    paused = int(getattr(result, "rowcount", 0) or 0)
    if paused:
        logger.warning(
            "campaigns paused: the number's quality dropped",
            extra={"tenant_id": str(tenant_id), "rating": rating, "campaigns": paused},
        )
    session.flush()
    return paused


def revoke_on_opt_out(
    session: Session, tenant_id: uuid.UUID, contact: Contact, at: datetime
) -> None:
    OptinService(session, tenant_id).revoke(contact, at=at)
