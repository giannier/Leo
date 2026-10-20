"""Requesting a WhatsApp number connection (TASK-017).

Three rules, and each one is enforced by the database rather than by a check
in front of it, because all three are races otherwise: two requests filed a
millisecond apart both read "available" and both proceed.

* **A number belongs to one business, anywhere on the platform** (RF-008).
  `whatsapp_numbers.phone_e164` is globally unique. Two businesses answering
  on the same number is not a conflict to resolve later; it is a customer
  talking to the wrong company.
* **One open request per number.** The partial unique index covers only
  `requested` and `in_progress`, so a customer whose request was rejected can
  correct their details and file again (Clarification 012).
* **The contact phone must be verified first** (Clarification 005). This is
  the anti-abuse gate: without it a throwaway account can queue work for a
  human being.

Global uniqueness is the one place a query here is deliberately **not**
tenant-scoped. It cannot be: the question is whether *any* business already
holds the number, and a scoped answer would always be no.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.user import User
from app.models.whatsapp import ConnectionRequest, WhatsAppNumber
from app.providers.whatsapp.base import NumberCredentials, WhatsAppError, WhatsAppProvider
from app.repositories.whatsapp import ConnectionRequestRepository, WhatsAppNumberRepository

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("requested", "in_progress")


class ConnectionError_(RuntimeError):
    """Base for every reason a connection request is refused."""


class PhoneNotVerified(ConnectionError_):
    """The caller has not proved control of their contact phone."""


class PhoneAlreadyConnected(ConnectionError_):
    """That number is already live for some business on the platform."""


class WebhookRegistrationFailed(ConnectionError_):
    """The provider would not create the webhook in the business's account."""


class OpenRequestExists(ConnectionError_):
    """A request for that number is already in the queue."""


@dataclass(frozen=True)
class GlobalNumberLookup:
    """Answers "is this number taken anywhere?", across every tenant.

    Deliberately not a `BaseRepository`: the whole question is platform-wide,
    and a tenant-scoped answer would always be "no" - which is the bug this
    guards against. It reads nothing but the phone column, so it cannot be
    used to enumerate another business's data.
    """

    session: Session

    def is_connected(self, phone_e164: str) -> bool:
        statement = select(WhatsAppNumber.id).where(WhatsAppNumber.phone_e164 == phone_e164)
        return self.session.execute(statement).first() is not None

    def has_open_request(self, phone_e164: str) -> bool:
        statement = select(ConnectionRequest.id).where(
            ConnectionRequest.phone_e164 == phone_e164,
            ConnectionRequest.status.in_(OPEN_STATUSES),
        )
        return self.session.execute(statement).first() is not None


class ConnectionService:
    """Connection requests for one tenant."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._requests = ConnectionRequestRepository(session, tenant_id)
        self._numbers = WhatsAppNumberRepository(session, tenant_id)
        self._global = GlobalNumberLookup(session)

    async def connect_ycloud(
        self,
        *,
        user: User,
        api_key: str,
        phone_e164: str,
        display_name: str,
        provider: WhatsAppProvider,
    ) -> WhatsAppNumber:
        """Connect a number with the business's own YCloud account, no human in between.

        The API key is the proof of ownership: only the account that holds the
        number can list it. Leo then creates the webhook in that account and keeps
        the secret YCloud returns, so the business never copies a URL or a secret
        by hand.

        Order matters. The webhook is created last among the remote calls and
        removed again if anything after it fails, so a refused connection does not
        leave a stray endpoint pointing at Leo in somebody's account.
        """
        if user.phone_verified_at is None:
            raise PhoneNotVerified("verify your contact phone before connecting a number")
        if self._global.is_connected(phone_e164):
            raise PhoneAlreadyConnected(phone_e164)

        found = await provider.discover_number(api_key=api_key, phone_e164=phone_e164)

        number = WhatsAppNumber(
            tenant_id=self._tenant_id,
            phone_e164=phone_e164,
            display_name=display_name,
            provider="ycloud",
            status="connected",
            webhook_token=secrets.token_hex(32),
        )
        base = get_settings().api_public_base_url.rstrip("/")
        url = f"{base}/webhooks/ycloud/{number.webhook_token}"
        try:
            registration = await provider.register_webhook(api_key=api_key, url=url)
        except WhatsAppError as exc:
            raise WebhookRegistrationFailed(str(exc)) from exc

        credentials = NumberCredentials(
            provider="ycloud",
            values={
                "api_key": api_key,
                "waba_id": found.waba_id,
                "phone_number_id": found.phone_number_id,
                "webhook_secret": registration.secret,
                "webhook_endpoint_id": registration.endpoint_id,
            },
        )
        try:
            # The global unique index decides a race for the same number.
            with self._session.begin_nested():
                self._session.add(number)
                self._session.flush()
            store_credentials(self._session, number, credentials=credentials, provider=provider)
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            await provider.remove_webhook(api_key=api_key, endpoint_id=registration.endpoint_id)
            if isinstance(exc, IntegrityError):
                raise PhoneAlreadyConnected(phone_e164) from exc
            raise
        return number

    def list_requests(self) -> list[ConnectionRequest]:
        return list(self._requests.list())

    def connected_number(self) -> WhatsAppNumber | None:
        numbers = self._numbers.list_connected()
        return numbers[0] if numbers else None

    def request(
        self,
        *,
        user: User,
        phone_e164: str,
        display_name: str,
        business_description: str | None = None,
        profile_photo_key: str | None = None,
        previous_request_id: uuid.UUID | None = None,
    ) -> ConnectionRequest:
        """File a request for an admin to connect this number.

        The pre-checks below exist to give a named error rather than a
        database exception; the constraints are what actually decide. Both are
        needed: the check alone races, and the constraint alone produces an
        error nobody can act on.
        """
        if user.phone_verified_at is None:
            raise PhoneNotVerified("verify your contact phone before requesting a number")

        if self._global.is_connected(phone_e164):
            raise PhoneAlreadyConnected(phone_e164)
        if self._global.has_open_request(phone_e164):
            raise OpenRequestExists(phone_e164)

        try:
            with self._session.begin_nested():
                request = self._requests.add(
                    phone_e164=phone_e164,
                    display_name=display_name,
                    business_description=business_description,
                    profile_photo_key=profile_photo_key,
                    previous_request_id=previous_request_id,
                    status="requested",
                )
        except IntegrityError as exc:
            # The partial unique index caught a request filed between our
            # check and our insert. Same answer, arrived at safely.
            raise OpenRequestExists(phone_e164) from exc

        self._session.commit()
        return request


def store_credentials(
    session: Session,
    number: WhatsAppNumber,
    *,
    credentials: NumberCredentials,
    provider: WhatsAppProvider,
) -> None:
    """Validate and encrypt a number's provider credentials (RF-009).

    Encrypted at the application layer with the key in the environment, so a
    database dump is not a set of working WhatsApp credentials for every
    business on the platform. Validated first: storing a placeholder somebody
    tabbed past would fail later, at the first customer message, where the
    cause is invisible.
    """
    provider.validate(credentials)
    number.provider = credentials.provider
    number.credentials_enc = encrypt_secret(_serialise(credentials))
    session.flush()


def read_credentials(number: WhatsAppNumber) -> NumberCredentials | None:
    """Decrypt a number's credentials. Never reaches an API response."""
    if number.credentials_enc is None:
        return None
    return _deserialise(number.provider, decrypt_secret(number.credentials_enc))


def _serialise(credentials: NumberCredentials) -> str:
    return json.dumps(credentials.values, sort_keys=True)


def _deserialise(provider: str, payload: str) -> NumberCredentials:
    return NumberCredentials(provider=provider, values=json.loads(payload))
