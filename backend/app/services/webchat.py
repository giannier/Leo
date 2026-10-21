"""The web chat: a channel that needs no WhatsApp number (feature 019, phase F).

A visitor opens the business's chat (its page, or the widget on its website), a session is created
for them (a contact that has no phone, `web:<uuid>`, and a conversation of channel `web`), and what
they write goes through the same turn pipeline as WhatsApp. Nothing is sent to a provider: an answer
is a row that the page reads by polling.

**Public means defended.** The endpoint needs no login, so it costs the business money to whoever
finds it: the chat is off until the owner turns it on, a session is created at most a few times per
address, a session may write only so fast, and the business has a daily cap. The public key only
says which business; the session token (signed, expiring) says which conversation, so a visitor can
read and write only their own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.models.web_chat import WebChat
from app.services.origin import normalize_origin

SESSION_DAYS = 7
MAX_TEXT = 500
EXTERNAL_PREFIX = "web:"


def new_public_key() -> str:
    """24 url-safe characters: enough that nobody guesses another business's chat."""
    return secrets.token_urlsafe(18)


class WebChatService:
    """The configuration of one business's chat."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def current(self) -> WebChat:
        """The business's chat, created (switched off) the first time it is asked for."""
        row = self._session.get(WebChat, self._tenant_id)
        if row is None:
            row = WebChat(tenant_id=self._tenant_id, public_key=new_public_key())
            self._session.add(row)
            self._session.flush()
            self._session.refresh(row)
        return row

    def rotate_key(self) -> WebChat:
        """A new address: the old one stops working at once."""
        row = self.current()
        row.public_key = new_public_key()
        self._session.flush()
        return row


def by_public_key(session: Session, key: str) -> WebChat | None:
    """The chat behind a public key. The one lookup that is not scoped to a business: the visitor
    has no business yet, only the key, and it answers nothing else."""
    if not key or len(key) > 64:
        return None
    return session.execute(select(WebChat).where(WebChat.public_key == key)).scalar_one_or_none()


# ----------------------------------------------------------------------- session


@dataclass(frozen=True)
class WebSession:
    tenant_id: uuid.UUID
    contact_id: uuid.UUID
    conversation_id: uuid.UUID


def _sign(payload: bytes) -> str:
    key = get_settings().secret_key.encode()
    return (
        base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )


def issue_token(session: WebSession, *, now: datetime | None = None) -> str:
    expires = (now or datetime.now(UTC)) + timedelta(days=SESSION_DAYS)
    body = json.dumps(
        {
            "t": str(session.tenant_id),
            "c": str(session.contact_id),
            "v": str(session.conversation_id),
            "e": int(expires.timestamp()),
        },
        separators=(",", ":"),
    ).encode()
    return f"{base64.urlsafe_b64encode(body).decode().rstrip('=')}.{_sign(body)}"


def read_token(token: str, *, now: datetime | None = None) -> WebSession | None:
    """The session a token names, or None if it is forged, malformed or expired."""
    try:
        encoded, signature = token.split(".", 1)
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if not hmac.compare_digest(_sign(body), signature):
            return None
        data = json.loads(body)
        if int(data["e"]) < int((now or datetime.now(UTC)).timestamp()):
            return None
        return WebSession(uuid.UUID(data["t"]), uuid.UUID(data["c"]), uuid.UUID(data["v"]))
    except (ValueError, KeyError, TypeError):
        return None


def open_session(session: Session, tenant: Tenant, *, origen: str | None = None) -> WebSession:
    """A new visitor: a contact without a phone and an open conversation of channel `web`.

    `origen` is what the link said (`?o=instagram`); without one the visit is simply "Chat web".
    """
    contact = Contact(
        tenant_id=tenant.id,
        external_user_id=f"{EXTERNAL_PREFIX}{uuid.uuid4()}",
        last_inbound_at=datetime.now(UTC),
        origen=normalize_origin(origen) or "Chat web",
    )
    session.add(contact)
    session.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="web", contact_id=contact.id, status="open"
    )
    session.add(conversation)
    session.flush()
    return WebSession(tenant.id, contact.id, conversation.id)
