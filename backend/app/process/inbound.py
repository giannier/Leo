"""What a stored inbound message remembers of its webhook event (Feature 004).

A tap on a button and the ad a customer came from arrive in the webhook, but the
turn that needs them runs after the debounce window and only has the message row.
`ChannelEventProcessor` therefore writes them to `messages.payload`, and the turn
pipeline reads them back here.

Pure functions, no I/O: what is stored comes from the provider (the webhook), so
reading it back is defensive.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from app.providers.whatsapp.base import InboundMessage, InteractiveReply, Referral

logger = logging.getLogger(__name__)


def payload_of(message: InboundMessage) -> dict[str, Any] | None:
    """The payload to store with an inbound message, or None for a plain one."""
    payload: dict[str, Any] = {}
    if message.interactive is not None:
        payload["interactive"] = asdict(message.interactive)
    if message.referral is not None:
        payload["referral"] = {
            key: value for key, value in asdict(message.referral).items() if value is not None
        }
    return payload or None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def interactive_of(payload: Mapping[str, Any] | None) -> InteractiveReply | None:
    """The tapped option stored with a message."""
    raw = (payload or {}).get("interactive")
    if not isinstance(raw, Mapping):
        return None
    reply_id = _text(raw.get("id"))
    if reply_id is None:
        logger.warning("stored interactive reply without an id")
        return None
    return InteractiveReply(
        id=reply_id,
        title=_text(raw.get("title")) or "",
        description=_text(raw.get("description")),
    )


def referral_of(payload: Mapping[str, Any] | None) -> Referral | None:
    """The ad stored with a message."""
    raw = (payload or {}).get("referral")
    if not isinstance(raw, Mapping):
        return None
    return Referral(
        source_id=_text(raw.get("source_id")),
        source_type=_text(raw.get("source_type")),
        source_url=_text(raw.get("source_url")),
        headline=_text(raw.get("headline")),
        body=_text(raw.get("body")),
        media_type=_text(raw.get("media_type")),
        media_url=_text(raw.get("media_url")),
        ctwa_clid=_text(raw.get("ctwa_clid")),
    )
