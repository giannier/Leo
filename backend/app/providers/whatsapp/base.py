"""The `WhatsAppProvider` port.

Everything this platform says to WhatsApp, and everything WhatsApp says back,
crosses this line and nowhere else (Constitution: third-party access lives in
`providers/`). Three jobs, and they are separate on purpose:

* **validate** credentials an admin pasted in, before a customer's first
  message proves them wrong;
* **send** a reply;
* **read** a webhook - both verifying it came from the provider and turning
  its payload into the vocabulary the rest of the application speaks.

The third is why the parsed types below exist. A pipeline written against
`payload["whatsappInboundMessage"]["customerProfile"]["name"]` is a pipeline
that has to be rewritten when the vendor changes, or when Feature 011's move
to Meta's own API arrives. `InboundMessage` and `StatusUpdate` are ours; what
the vendor called them stops at this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class WhatsAppError(RuntimeError):
    """The provider could not complete the operation.

    `retryable` is the only thing callers branch on. Sending is retried with
    backoff (HU-02: three attempts), and the difference between "the gateway
    hiccupped" and "this credential is wrong" is the difference between a
    message that arrives a second late and one that is retried three times
    before failing exactly as it would have the first time.
    """

    retryable = False


class InvalidCredentials(WhatsAppError):
    """The credentials supplied do not describe a usable number."""


class WhatsAppAuthError(WhatsAppError):
    """The provider rejected the credential. Retrying will not help."""


class WhatsAppRateLimited(WhatsAppError):
    """The provider is throttling us. The same request will work later."""

    retryable = True


class WhatsAppTransientError(WhatsAppError):
    """A timeout, a dropped connection, or the provider's own 5xx."""

    retryable = True


@dataclass(frozen=True)
class NumberCredentials:
    """What a provider needs to send on a number's behalf.

    Deliberately a free-form mapping rather than named fields: YCloud, Meta
    and any future gateway name these differently, and inventing a canonical
    shape now would mean guessing at three vendors from one.
    """

    provider: str
    values: dict[str, str]


@dataclass(frozen=True)
class SendResult:
    """What the provider said when it accepted a message.

    `provider_message_id` is the handle every later status webhook uses, so
    it has to be stored with the message or the delivery states arrive with
    nothing to attach them to.
    """

    provider_message_id: str
    status: str


@dataclass(frozen=True)
class InboundMessage:
    """A message from a customer, in this application's own vocabulary."""

    provider_message_id: str
    # The customer. `to_phone` is the tenant's own number, and is what the
    # pipeline uses to work out which tenant this belongs to.
    from_phone: str
    to_phone: str
    profile_name: str | None
    # Already mapped to `models.conversation.MESSAGE_TYPES`; anything this
    # version cannot read arrives as `unsupported` rather than being dropped.
    msg_type: str
    text: str | None
    sent_at: datetime


@dataclass(frozen=True)
class StatusUpdate:
    """A delivery report for a message this platform sent."""

    provider_message_id: str
    # Already mapped to `models.conversation.DELIVERY_STATUSES`.
    status: str
    occurred_at: datetime | None
    error: str | None = None


@dataclass(frozen=True)
class QualityUpdate:
    """The provider's verdict on a number's standing with Meta.

    A drop to `RED` or `FLAGGED` is the early warning before a number stops
    being able to send at all, so it is a first-class parse result rather
    than something dug out of a payload later (HU-03).
    """

    phone_e164: str
    quality_rating: str


# What `parse_event` can return. `None` means "a valid event this version has
# no use for" - a template status, a payment notice - which is not an error
# and must not be retried.
ParsedEvent = InboundMessage | StatusUpdate | QualityUpdate | None


@runtime_checkable
class WhatsAppProvider(Protocol):
    @property
    def name(self) -> str: ...

    def validate(self, credentials: NumberCredentials) -> None:
        """Raise `InvalidCredentials` if these could not possibly work.

        Cheap structural checks only. Whether the number actually sends is
        proved by sending.
        """
        ...

    async def send_text(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: str,
        body: str,
    ) -> SendResult:
        """Send one text message, or raise a `WhatsAppError`."""
        ...

    def verify_signature(self, *, raw_body: bytes, header: str | None, secret: str) -> bool:
        """Whether this delivery really came from the provider."""
        ...

    def parse_event(self, payload: dict[str, object]) -> ParsedEvent:
        """Translate one webhook payload, or return None if it is not for us."""
        ...

    def event_id(self, payload: dict[str, object]) -> str | None:
        """The provider's own id for this delivery, used for idempotency."""
        ...
