"""The YCloud adapter: sending, signatures, and reading what comes back.

Everything here runs against recorded payloads and a stubbed transport. No
network, no credential, and - importantly - no real WhatsApp message sent to a
real person, which is the reason this adapter could not be tested at all
before it grew a `transport` seam.

Two things are being pinned, and they fail very differently:

* **The parser must not be strict.** A vendor field that changes shape has to
  degrade to `unsupported`, never to an exception, because the raw event is
  already stored and can be replayed once the parser learns the shape. An
  exception here is a customer's message that never arrives.
* **The signature check must not be lenient.** It is the only thing standing
  between the public internet and the inbox of every business on the platform.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.providers.whatsapp import (
    InboundMessage,
    InvalidCredentials,
    NumberCredentials,
    QualityUpdate,
    StatusUpdate,
    WhatsAppAuthError,
    WhatsAppError,
    WhatsAppRateLimited,
    WhatsAppTransientError,
    YCloudProvider,
)
from tests.fixtures import load, load_raw

SECRET = "whsec_a_test_secret_nobody_uses"
SENDER = "+51912345678"
CUSTOMER = "+51987654321"

ACCEPTED = {
    "id": "wamid.HBgLNTE5ODc2NTQzMjEVAgARGBI5QjZDN0Q4RTlGMEExQjJDM0QA",
    "status": "accepted",
}


def _credentials(**overrides: str) -> NumberCredentials:
    values = {
        "waba_id": "102290129340398",
        "phone_number_id": "112233445566778",
        "api_key": "yc_live_abcdef123456",
    }
    values.update(overrides)
    return NumberCredentials(provider="ycloud", values=values)


def _provider(
    response: httpx.Response,
) -> tuple[YCloudProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return YCloudProvider(transport=httpx.MockTransport(handler)), seen


def _sign(raw: bytes, *, secret: str = SECRET, timestamp: str = "1785000000") -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256)
    return f"t={timestamp},s={digest.hexdigest()}"


def _json(request: httpx.Request) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(request.content)
    return decoded


class TestSending:
    async def test_the_request_is_the_one_ycloud_documents(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_text(
            _credentials(), sender=SENDER, to=CUSTOMER, body="Hola, si tenemos."
        )

        request = seen[0]
        assert str(request.url) == "https://api.ycloud.com/v2/whatsapp/messages"
        assert request.headers["x-api-key"] == "yc_live_abcdef123456"
        assert _json(request) == {
            "from": SENDER,
            "to": CUSTOMER,
            "type": "text",
            "text": {"body": "Hola, si tenemos."},
        }

    async def test_the_provider_message_id_comes_back(self) -> None:
        """Every later delivery webhook names the message by this id. Losing
        it means the status updates arrive with nothing to attach them to."""
        provider, _ = _provider(httpx.Response(201, json=ACCEPTED))

        result = await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")

        assert result.provider_message_id == ACCEPTED["id"]

    async def test_an_accepted_send_with_no_id_is_not_a_success(self) -> None:
        provider, _ = _provider(httpx.Response(201, json={"status": "accepted"}))

        with pytest.raises(WhatsAppError, match="no id"):
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")

    async def test_credentials_are_checked_before_anything_is_sent(self) -> None:
        """A missing key would otherwise be discovered as a 401 from the
        gateway, one customer message later."""
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))
        broken = _credentials(api_key="")

        with pytest.raises(InvalidCredentials):
            await provider.send_text(broken, sender=SENDER, to=CUSTOMER, body="Hola")
        assert seen == []


class TestWhenTheGatewayRefuses:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_credential_is_not_retryable(self, status: int) -> None:
        """Three attempts against a wrong key is three times the same answer,
        one customer's reply later than the first."""
        provider, _ = _provider(httpx.Response(status, json={"message": "bad key"}))

        with pytest.raises(WhatsAppAuthError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")
        assert caught.value.retryable is False

    async def test_the_rejection_never_echoes_the_key(self) -> None:
        """A gateway that refuses a credential sometimes reflects it back, and
        this message goes to a log."""
        provider, _ = _provider(
            httpx.Response(401, json={"message": "invalid key yc_live_abcdef123456"})
        )

        with pytest.raises(WhatsAppAuthError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")
        assert "yc_live_abcdef123456" not in str(caught.value)

    async def test_throttling_is_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(429, json={"message": "slow down"}))

        with pytest.raises(WhatsAppRateLimited) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")
        assert caught.value.retryable is True

    async def test_the_gateways_own_failure_is_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(502, text="bad gateway"))

        with pytest.raises(WhatsAppTransientError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")
        assert caught.value.retryable is True

    async def test_a_refused_message_is_not_retryable(self) -> None:
        """A 4xx is our mistake - a malformed number, a message outside the
        session window - and the identical request produces the identical
        refusal."""
        provider, _ = _provider(httpx.Response(400, json={"message": "invalid recipient"}))

        with pytest.raises(WhatsAppError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")
        assert caught.value.retryable is False

    async def test_a_timeout_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        provider = YCloudProvider(transport=httpx.MockTransport(handler))

        with pytest.raises(WhatsAppTransientError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=CUSTOMER, body="Hola")
        assert caught.value.retryable is True


class TestTheSignature:
    """The only thing between the public internet and every tenant's inbox."""

    def test_a_genuine_delivery_is_accepted(self) -> None:
        raw = load_raw("ycloud/inbound_text.json")

        assert (
            YCloudProvider().verify_signature(raw_body=raw, header=_sign(raw), secret=SECRET)
            is True
        )

    def test_a_tampered_body_is_rejected(self) -> None:
        """The attack this exists for: a valid capture with the phone number
        swapped, replayed at the endpoint."""
        raw = load_raw("ycloud/inbound_text.json")
        header = _sign(raw)
        tampered = raw.replace(b"+51987654321", b"+51900000000")

        assert (
            YCloudProvider().verify_signature(raw_body=tampered, header=header, secret=SECRET)
            is False
        )

    def test_a_signature_from_another_secret_is_rejected(self) -> None:
        raw = load_raw("ycloud/inbound_text.json")

        assert (
            YCloudProvider().verify_signature(
                raw_body=raw, header=_sign(raw, secret="somebody-elses"), secret=SECRET
            )
            is False
        )

    def test_the_timestamp_is_part_of_what_is_signed(self) -> None:
        """Signing the body alone would let a captured signature be replayed
        against a body it no longer matches in time."""
        raw = load_raw("ycloud/inbound_text.json")
        header = _sign(raw, timestamp="1785000000").replace("t=1785000000", "t=1785009999")

        assert (
            YCloudProvider().verify_signature(raw_body=raw, header=header, secret=SECRET) is False
        )

    @pytest.mark.parametrize("header", [None, "", "nonsense", "t=1785000000", "s=abc", "t=,s="])
    def test_a_malformed_header_is_rejected_rather_than_crashing(self, header: str | None) -> None:
        """The endpoint is public, so this is reachable by anyone. A parser
        that raises here turns a 401 into a 500 and an easy denial of service."""
        assert (
            YCloudProvider().verify_signature(raw_body=b"{}", header=header, secret=SECRET) is False
        )

    def test_an_unconfigured_secret_refuses_everything(self) -> None:
        """The correct failure. An endpoint with no secret configured is a
        public write API to every tenant's inbox."""
        raw = load_raw("ycloud/inbound_text.json")

        assert (
            YCloudProvider().verify_signature(raw_body=raw, header=_sign(raw), secret="") is False
        )

    def test_re_serialising_the_body_would_not_verify(self) -> None:
        """Pins why the endpoint must keep the raw bytes. `json.dumps` of the
        parsed payload is a different string, and a check written against it
        would reject every genuine delivery - which then gets 'fixed' by
        removing the check."""
        raw = load_raw("ycloud/inbound_text.json")
        header = _sign(raw)
        reserialised = json.dumps(json.loads(raw)).encode()

        assert (
            YCloudProvider().verify_signature(raw_body=reserialised, header=header, secret=SECRET)
            is False
        )


class TestReadingAnInboundMessage:
    def test_a_text_message_arrives_whole(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_text.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.from_phone == CUSTOMER
        assert parsed.to_phone == SENDER
        assert parsed.profile_name == "Ana Quispe"
        assert parsed.msg_type == "text"
        assert parsed.text == "Hola, tienen pan integral?"

    def test_the_send_time_is_the_customers_not_ours(self) -> None:
        """It sets `last_inbound_at`, which is what the 24-hour window is
        computed from. Using our own receipt time would quietly extend a
        window Meta measures from theirs."""
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_text.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.sent_at == datetime(2026, 8, 4, 14, 32, 10, tzinfo=UTC)

    def test_a_media_message_keeps_its_type_and_carries_no_text(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_image.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.msg_type == "image"
        assert parsed.text is None

    def test_a_type_this_version_cannot_read_becomes_unsupported(self) -> None:
        """C-001: stored, answered with the tenant's fallback, and counted as
        an interaction. Never dropped, and never an exception."""
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_video.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.msg_type == "unsupported"
        assert parsed.from_phone == CUSTOMER

    def test_a_missing_profile_name_is_not_an_error(self) -> None:
        """WhatsApp omits it for people who have not set one."""
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_video.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.profile_name is None


class TestReadingADeliveryReport:
    def test_a_delivered_report_is_translated(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/message_delivered.json"))

        assert isinstance(parsed, StatusUpdate)
        assert parsed.status == "delivered"
        assert parsed.provider_message_id.startswith("wamid.")
        assert parsed.error is None

    def test_a_failure_carries_the_reason_a_person_can_read(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/message_failed.json"))

        assert isinstance(parsed, StatusUpdate)
        assert parsed.status == "failed"
        assert parsed.error is not None
        assert "24 hours" in parsed.error


class TestReadingAQualityChange:
    def test_a_drop_to_red_is_a_first_class_result(self) -> None:
        """The early warning before a number stops being able to send at all
        (HU-03)."""
        parsed = YCloudProvider().parse_event(load("ycloud/quality_updated.json"))

        assert isinstance(parsed, QualityUpdate)
        assert parsed.phone_e164 == SENDER
        assert parsed.quality_rating == "RED"


class TestEventsThisVersionIgnores:
    def test_a_template_update_is_not_an_error(self) -> None:
        """Templates arrive with Feature 011. Until then this is a complete,
        valid event with no behaviour attached - which must not be retried,
        and must not become a dead letter."""
        assert YCloudProvider().parse_event(load("ycloud/template_status.json")) is None

    def test_the_event_id_is_read_for_idempotency(self) -> None:
        payload = load("ycloud/inbound_text.json")

        assert YCloudProvider().event_id(payload) == payload["id"]

    def test_an_event_with_no_id_is_refused_rather_than_invented(self) -> None:
        """Storing it under a generated id would make the provider's retry
        look like a second message."""
        assert YCloudProvider().event_id({"type": "whatsapp.message.updated"}) is None


class TestTheParserDoesNotCrashOnRubbish:
    """`plan.md`'s risk: "payloads de YCloud distintos a la doc". The answer
    is not to guess harder - the raw event is stored and can be replayed - it
    is to make sure a surprise never takes the endpoint down."""

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"type": "whatsapp.inbound_message.received"},
            {"type": "whatsapp.inbound_message.received", "whatsappInboundMessage": None},
            {"type": "whatsapp.inbound_message.received", "whatsappInboundMessage": {}},
            {"type": "whatsapp.message.updated", "whatsappMessage": {"id": "x"}},
            {"type": "whatsapp.message.updated", "whatsappMessage": {"status": "invented"}},
            {"type": 12345},
        ],
    )
    def test_it_returns_none_instead_of_raising(self, payload: dict[str, Any]) -> None:
        assert YCloudProvider().parse_event(payload) is None

    def test_a_text_object_that_is_not_an_object_is_survivable(self) -> None:
        """A vendor sending `null` where an object was documented is a
        Tuesday, and it must not take the message down with it."""
        payload = load("ycloud/inbound_text.json")
        payload["whatsappInboundMessage"]["text"] = None

        parsed = YCloudProvider().parse_event(payload)

        assert isinstance(parsed, InboundMessage)
        assert parsed.text is None

    def test_an_unparseable_timestamp_does_not_close_the_window(self) -> None:
        """Falling back to now is deliberate. Raising, or defaulting to the
        epoch, would close a 24-hour window on a message that just arrived."""
        payload = load("ycloud/inbound_text.json")
        payload["whatsappInboundMessage"]["sendTime"] = "the day before yesterday"
        before = datetime.now(UTC)

        parsed = YCloudProvider().parse_event(payload)

        assert isinstance(parsed, InboundMessage)
        assert parsed.sent_at >= before
