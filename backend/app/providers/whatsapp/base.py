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
from typing import Literal, Protocol, runtime_checkable

# WhatsApp's own limits for interactive messages, whichever gateway carries
# them. The sales tools truncate to these before sending; the adapter checks them
# again so a request Meta would refuse never leaves the building.
MAX_BUTTONS = 3
MAX_LIST_ROWS = 10
MAX_BUTTON_TITLE = 20
MAX_ROW_TITLE = 24
MAX_ROW_DESCRIPTION = 72
MAX_BODY = 1024
MAX_HEADER_FOOTER = 60
MAX_LIST_BUTTON = 20


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
class Recipient:
    """Who a message goes to: a phone number, a BSUID, or both.

    Since Meta's business-scoped user ids a customer may write without ever
    sharing a number, so the reply cannot assume one. The phone wins when both
    are known (YCloud itself ignores `recipient` when `to` is present); the
    BSUID is what makes a contact without a phone reachable at all.
    """

    phone: str | None = None
    bsuid: str | None = None

    def __post_init__(self) -> None:
        if not (self.phone or self.bsuid):
            raise ValueError("a recipient needs a phone number or a BSUID")


MediaKind = Literal["image", "document", "video", "audio"]


@dataclass(frozen=True)
class MediaRef:
    """A file to send: already uploaded (`media_id`) or reachable (`link`).

    Exactly one of the two. `filename` is only meaningful for documents, where
    it is what the customer sees on the attachment.
    """

    kind: MediaKind
    media_id: str | None = None
    link: str | None = None
    filename: str | None = None

    def __post_init__(self) -> None:
        if bool(self.media_id) == bool(self.link):
            raise ValueError("a media reference needs exactly one of media_id or link")


@dataclass(frozen=True)
class UploadedMedia:
    """What the provider kept when a file was uploaded to it."""

    media_id: str
    # When the provider forgets the file; the platform re-uploads before then.
    expires_at: datetime


@dataclass(frozen=True)
class Button:
    """One reply button. WhatsApp allows 1 to 3, with titles up to 20 characters."""

    id: str
    title: str


@dataclass(frozen=True)
class ListRow:
    """One selectable row of a list (title up to 24 characters, description up to 72)."""

    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True)
class ListSection:
    """A group of rows. WhatsApp allows 10 rows across all the sections."""

    rows: tuple[ListRow, ...]
    title: str | None = None


@dataclass(frozen=True)
class InteractiveReply:
    """A `button_reply` or `list_reply`: what the customer tapped."""

    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True)
class Referral:
    """Where a customer came from when they tapped a Click-to-WhatsApp ad.

    YCloud's field names, mapped onto ours. `contracts/api.md` section 8 listed
    `ad_id` and `media_url`; the provider's documentation calls them
    `source_id` and `image_url` / `video_url`, and the documentation wins.
    """

    source_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    headline: str | None = None
    body: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    ctwa_clid: str | None = None


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
    # pipeline uses to work out which tenant this belongs to. The customer's
    # phone can be absent (they wrote through a BSUID and never shared it), so
    # at least one of `from_phone` and `from_user_id` is always set.
    from_phone: str | None
    to_phone: str
    profile_name: str | None
    # Already mapped to `models.conversation.MESSAGE_TYPES`; anything this
    # version cannot read arrives as `unsupported` rather than being dropped.
    # A tap on a button or a list row arrives as `text` carrying the title.
    msg_type: str
    text: str | None
    sent_at: datetime
    # Meta's business-scoped user id (BSUID), when the webhook carries one.
    from_user_id: str | None = None
    # The tapped option, with the id the assistant gave it (`camino:...`).
    interactive: InteractiveReply | None = None
    # Present on the first message after a Click-to-WhatsApp ad.
    referral: Referral | None = None


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
        to: Recipient,
        body: str,
    ) -> SendResult:
        """Send one text message, or raise a `WhatsAppError`."""
        ...

    async def send_media(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        media: MediaRef,
        caption: str | None = None,
    ) -> SendResult:
        """Send one image, document, video or audio."""
        ...

    async def send_buttons(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        body: str,
        buttons: list[Button],
        header: str | None = None,
        footer: str | None = None,
    ) -> SendResult:
        """Send a message with 1 to 3 reply buttons."""
        ...

    async def send_list(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        to: Recipient,
        body: str,
        button_text: str,
        sections: list[ListSection],
    ) -> SendResult:
        """Send a message with a list of up to 10 selectable rows."""
        ...

    async def upload_media(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        data: bytes,
        mime: str,
        filename: str,
    ) -> UploadedMedia:
        """Store a file with the provider so it can be sent by id."""
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
