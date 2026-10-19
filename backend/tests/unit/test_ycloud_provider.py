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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.providers.whatsapp import (
    Button,
    InboundMedia,
    InboundMessage,
    InteractiveReply,
    InvalidCredentials,
    ListRow,
    ListSection,
    MediaRef,
    NumberCredentials,
    QualityUpdate,
    Recipient,
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
TO = Recipient(phone=CUSTOMER)

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

        await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola, si tenemos.")

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

        result = await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")

        assert result.provider_message_id == ACCEPTED["id"]

    async def test_an_accepted_send_with_no_id_is_not_a_success(self) -> None:
        provider, _ = _provider(httpx.Response(201, json={"status": "accepted"}))

        with pytest.raises(WhatsAppError, match="no id"):
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")

    async def test_credentials_are_checked_before_anything_is_sent(self) -> None:
        """A missing key would otherwise be discovered as a 401 from the
        gateway, one customer message later."""
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))
        broken = _credentials(api_key="")

        with pytest.raises(InvalidCredentials):
            await provider.send_text(broken, sender=SENDER, to=TO, body="Hola")
        assert seen == []


class TestWhenTheGatewayRefuses:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_credential_is_not_retryable(self, status: int) -> None:
        """Three attempts against a wrong key is three times the same answer,
        one customer's reply later than the first."""
        provider, _ = _provider(httpx.Response(status, json={"message": "bad key"}))

        with pytest.raises(WhatsAppAuthError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")
        assert caught.value.retryable is False

    async def test_the_rejection_never_echoes_the_key(self) -> None:
        """A gateway that refuses a credential sometimes reflects it back, and
        this message goes to a log."""
        provider, _ = _provider(
            httpx.Response(401, json={"message": "invalid key yc_live_abcdef123456"})
        )

        with pytest.raises(WhatsAppAuthError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")
        assert "yc_live_abcdef123456" not in str(caught.value)

    async def test_throttling_is_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(429, json={"message": "slow down"}))

        with pytest.raises(WhatsAppRateLimited) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")
        assert caught.value.retryable is True

    async def test_the_gateways_own_failure_is_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(502, text="bad gateway"))

        with pytest.raises(WhatsAppTransientError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")
        assert caught.value.retryable is True

    async def test_a_refused_message_is_not_retryable(self) -> None:
        """A 4xx is our mistake - a malformed number, a message outside the
        session window - and the identical request produces the identical
        refusal."""
        provider, _ = _provider(httpx.Response(400, json={"message": "invalid recipient"}))

        with pytest.raises(WhatsAppError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")
        assert caught.value.retryable is False

    async def test_a_timeout_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        provider = YCloudProvider(transport=httpx.MockTransport(handler))

        with pytest.raises(WhatsAppTransientError) as caught:
            await provider.send_text(_credentials(), sender=SENDER, to=TO, body="Hola")
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

    def test_an_image_keeps_the_reference_to_its_file(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_image.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.media == InboundMedia(
            media_id="1642901234567890",
            link="https://api.ycloud.com/v2/whatsapp/media/download/1642901234567890",
            mime_type="image/jpeg",
            caption="Este es el que quiero",
        )

    def test_the_camel_case_mime_type_is_accepted_too(self) -> None:
        payload = load("ycloud/inbound_image.json")
        image = payload["whatsappInboundMessage"]["image"]
        image["mimeType"] = image.pop("mime_type")

        parsed = YCloudProvider().parse_event(payload)

        assert isinstance(parsed, InboundMessage)
        assert parsed.media is not None and parsed.media.mime_type == "image/jpeg"

    def test_an_image_object_without_id_or_link_is_no_media(self) -> None:
        payload = load("ycloud/inbound_image.json")
        payload["whatsappInboundMessage"]["image"] = {"caption": "sin archivo"}

        parsed = YCloudProvider().parse_event(payload)

        assert isinstance(parsed, InboundMessage)
        assert parsed.msg_type == "image" and parsed.media is None

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
    def test_a_template_update_is_read_since_feature_011(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/template_status.json"))
        assert parsed is not None and getattr(parsed, "status", None) == "approved"

    def test_an_event_with_no_behaviour_is_not_an_error(self) -> None:
        """Complete and valid, with nothing attached: not retried, not a dead letter."""
        assert (
            YCloudProvider().parse_event({"id": "evt_x", "type": "whatsapp.payment.updated"})
            is None
        )

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


# ------------------------------------------------- interactive, media, BSUID

BSUID = "US.13491208655302741918"


class TestSendingInteractiveAndMedia:
    """Contract tests: the request bodies are the recorded fixtures, so a drift
    from YCloud's documented shape fails here."""

    async def test_buttons_match_the_recorded_request(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_buttons(
            _credentials(),
            sender=SENDER,
            to=TO,
            body="Hola Ana, que necesitas?",
            buttons=[
                Button("camino:comprar", "Quiero comprar"),
                Button("camino:asesoria", "Necesito asesoria"),
            ],
        )

        assert _json(seen[0]) == load("ycloud/send_buttons_request.json")

    async def test_header_and_footer_are_sent_only_when_given(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_buttons(
            _credentials(),
            sender=SENDER,
            to=TO,
            body="Elige",
            buttons=[Button("a", "A")],
            header="Titulo",
            footer="Pie",
        )

        interactive = _json(seen[0])["interactive"]
        assert interactive["header"] == {"type": "text", "text": "Titulo"}
        assert interactive["footer"] == {"text": "Pie"}

    async def test_a_list_matches_the_recorded_request(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_list(
            _credentials(),
            sender=SENDER,
            to=TO,
            body="Estos son nuestros paquetes",
            button_text="Ver paquetes",
            sections=[
                ListSection(
                    rows=(
                        ListRow(
                            "producto:5f0c1c1e-8c6b-4f62-9f0e-2f4b0d9f3a11",
                            "Paquete 5 certificados",
                            "S/ 500.00",
                        ),
                        ListRow(
                            "producto:7a1d2e3f-9d7c-4a73-8a1f-3a5c1e0a4b22",
                            "Paquete 10 certificados",
                        ),
                    )
                )
            ],
        )

        assert _json(seen[0]) == load("ycloud/send_list_request.json")

    async def test_a_document_by_media_id_matches_the_recorded_request(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_media(
            _credentials(),
            sender=SENDER,
            to=TO,
            media=MediaRef("document", media_id="1642901234567891", filename="malla.pdf"),
            caption="Malla curricular",
        )

        assert _json(seen[0]) == load("ycloud/send_media_request.json")

    async def test_an_image_by_link_and_audio_never_carries_a_caption(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))
        link = "https://files.example.com/a.jpg?sig=1"

        await provider.send_media(
            _credentials(),
            sender=SENDER,
            to=TO,
            media=MediaRef("image", link=link, filename="ignored.jpg"),
            caption="Mira",
        )
        await provider.send_media(
            _credentials(),
            sender=SENDER,
            to=TO,
            media=MediaRef("audio", media_id="9"),
            caption="no va",
        )

        assert _json(seen[0])["image"] == {"link": link, "caption": "Mira"}
        assert _json(seen[1])["audio"] == {"id": "9"}

    @pytest.mark.parametrize(
        "case",
        ["four_buttons", "no_buttons", "long_title", "long_body", "eleven_rows", "long_row"],
    )
    async def test_a_request_whatsapp_would_refuse_never_leaves(self, case: str) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))
        to = Recipient(phone=CUSTOMER)
        four = [Button(str(i), "B") for i in range(4)]
        rows = tuple(ListRow(str(i), "R") for i in range(11))
        credentials = _credentials()

        with pytest.raises(WhatsAppError) as caught:
            if case == "four_buttons":
                await provider.send_buttons(
                    credentials, sender=SENDER, to=to, body="x", buttons=four
                )
            elif case == "no_buttons":
                await provider.send_buttons(credentials, sender=SENDER, to=to, body="x", buttons=[])
            elif case == "long_title":
                await provider.send_buttons(
                    credentials, sender=SENDER, to=to, body="x", buttons=[Button("a", "x" * 21)]
                )
            elif case == "long_body":
                await provider.send_buttons(
                    credentials, sender=SENDER, to=to, body="x" * 1025, buttons=four[:1]
                )
            elif case == "eleven_rows":
                await provider.send_list(
                    credentials,
                    sender=SENDER,
                    to=to,
                    body="x",
                    button_text="Ver",
                    sections=[ListSection(rows=rows)],
                )
            else:
                await provider.send_list(
                    credentials,
                    sender=SENDER,
                    to=to,
                    body="x",
                    button_text="Ver",
                    sections=[ListSection(rows=(ListRow("a", "x" * 25),))],
                )

        assert caught.value.retryable is False
        assert seen == []


class TestAddressingByBsuid:
    async def test_a_contact_without_a_phone_is_addressed_by_recipient(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_text(
            _credentials(),
            sender=SENDER,
            to=Recipient(bsuid=BSUID),
            body="Hola, con gusto te ayudo.",
        )

        assert _json(seen[0]) == load("ycloud/send_to_bsuid_request.json")

    async def test_the_phone_wins_when_both_are_known(self) -> None:
        provider, seen = _provider(httpx.Response(201, json=ACCEPTED))

        await provider.send_text(
            _credentials(),
            sender=SENDER,
            to=Recipient(phone=CUSTOMER, bsuid=BSUID),
            body="Hola",
        )

        body = _json(seen[0])
        assert body["to"] == CUSTOMER
        assert "recipient" not in body

    def test_a_recipient_needs_one_identifier(self) -> None:
        with pytest.raises(ValueError):
            Recipient()

    def test_a_media_reference_needs_exactly_one_source(self) -> None:
        with pytest.raises(ValueError):
            MediaRef("image")
        with pytest.raises(ValueError):
            MediaRef("image", media_id="1", link="https://x.example/a.jpg")


class TestUploadingMedia:
    async def test_the_file_goes_up_as_multipart_to_the_senders_path(self) -> None:
        provider, seen = _provider(
            httpx.Response(200, json=load("ycloud/upload_media_response.json"))
        )
        before = datetime.now(UTC)

        uploaded = await provider.upload_media(
            _credentials(),
            sender=SENDER,
            data=b"%PDF-1.4 hi",
            mime="application/pdf",
            filename="m.pdf",
        )

        request = seen[0]
        assert str(request.url) == "https://api.ycloud.com/v2/whatsapp/media/%2B51912345678/upload"
        assert request.headers["x-api-key"] == "yc_live_abcdef123456"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        assert b'name="file"; filename="m.pdf"' in request.content
        assert b"application/pdf" in request.content
        assert b"%PDF-1.4 hi" in request.content
        assert uploaded.media_id == "1642901234567891"
        # YCloud keeps the file 30 days.
        assert before + timedelta(days=29, hours=23) < uploaded.expires_at

    async def test_an_answer_without_an_id_is_not_a_success(self) -> None:
        provider, _ = _provider(httpx.Response(200, json={}))

        with pytest.raises(WhatsAppError):
            await provider.upload_media(
                _credentials(), sender=SENDER, data=b"x", mime="image/png", filename="a.png"
            )

    async def test_a_gateway_failure_is_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(503))

        with pytest.raises(WhatsAppTransientError):
            await provider.upload_media(
                _credentials(), sender=SENDER, data=b"x", mime="image/png", filename="a.png"
            )


class TestReadingInteractiveReferralAndBsuid:
    def test_a_button_tap_becomes_text_plus_the_tapped_option(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_interactive_button.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.msg_type == "text"
        assert parsed.text == "Quiero comprar"
        assert parsed.interactive == InteractiveReply("camino:comprar", "Quiero comprar")
        assert parsed.from_user_id == "PE.13491208655302741918"
        assert parsed.from_phone == CUSTOMER

    def test_a_list_tap_keeps_the_row_description(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_interactive_list.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.interactive == InteractiveReply(
            "producto:5f0c1c1e-8c6b-4f62-9f0e-2f4b0d9f3a11",
            "Paquete 5 certificados",
            "S/ 500.00",
        )

    def test_an_unreadable_interactive_message_is_unsupported_not_an_error(self) -> None:
        payload = load("ycloud/inbound_interactive_button.json")
        payload["whatsappInboundMessage"]["interactive"] = {"type": "nfm_reply"}

        parsed = YCloudProvider().parse_event(payload)

        assert isinstance(parsed, InboundMessage)
        assert parsed.msg_type == "unsupported"
        assert parsed.interactive is None

    def test_an_ad_referral_is_read_with_the_documented_names(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_referral.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.text == "Hola, quiero informacion de este anuncio"
        assert parsed.referral is not None
        assert parsed.referral.source_id == "120210000000000001"
        assert parsed.referral.source_type == "ad"
        assert parsed.referral.source_url == "https://fb.me/3AbCdEf"
        assert parsed.referral.headline == "Certificaciones SIAF con 30% de descuento"
        assert parsed.referral.media_url == "https://scontent.xx.fbcdn.net/v/anuncio.jpg"
        assert parsed.referral.ctwa_clid is not None

    def test_camel_case_referral_keys_are_accepted_too(self) -> None:
        payload = load("ycloud/inbound_referral.json")
        payload["whatsappInboundMessage"]["referral"] = {
            "sourceUrl": "https://fb.me/x",
            "sourceId": "42",
            "videoUrl": "https://cdn.example/v.mp4",
        }

        parsed = YCloudProvider().parse_event(payload)

        assert isinstance(parsed, InboundMessage)
        assert parsed.referral is not None
        assert parsed.referral.source_id == "42"
        assert parsed.referral.media_url == "https://cdn.example/v.mp4"

    def test_a_message_without_a_referral_has_none(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_text.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.referral is None

    def test_a_customer_who_never_shared_a_number_is_identified_by_bsuid(self) -> None:
        parsed = YCloudProvider().parse_event(load("ycloud/inbound_bsuid_only.json"))

        assert isinstance(parsed, InboundMessage)
        assert parsed.from_phone is None
        assert parsed.from_user_id == BSUID
        assert parsed.text == "Hola, quiero ver los paquetes"

    def test_a_message_with_no_sender_at_all_is_still_rejected(self) -> None:
        payload = load("ycloud/inbound_bsuid_only.json")
        del payload["whatsappInboundMessage"]["fromUserId"]

        assert YCloudProvider().parse_event(payload) is None


class TestDownloadingACustomersFile:
    LINK = "https://api.ycloud.com/v2/whatsapp/media/download/1642901234567890"

    async def test_it_fetches_the_file_with_the_tenants_key(self) -> None:
        provider, seen = _provider(
            httpx.Response(200, content=b"jpegbytes", headers={"content-type": "image/jpeg"})
        )

        media = await provider.download_media(_credentials(), link=self.LINK, max_bytes=1000)

        assert media.data == b"jpegbytes" and media.mime_type == "image/jpeg"
        assert str(seen[0].url) == self.LINK
        assert seen[0].headers["x-api-key"] == "yc_live_abcdef123456"

    @pytest.mark.parametrize(
        "link",
        [
            "https://evil.example/v2/whatsapp/media/download/1",
            "http://api.ycloud.com/v2/whatsapp/media/download/1",
            "not a url",
        ],
    )
    async def test_it_never_sends_the_key_to_another_host(self, link: str) -> None:
        provider, seen = _provider(httpx.Response(200, content=b"x"))

        with pytest.raises(WhatsAppError):
            await provider.download_media(_credentials(), link=link, max_bytes=1000)

        assert seen == []

    async def test_a_file_over_the_limit_is_refused(self) -> None:
        provider, _ = _provider(httpx.Response(200, content=b"x" * 50))

        with pytest.raises(WhatsAppError, match="larger"):
            await provider.download_media(_credentials(), link=self.LINK, max_bytes=10)

    async def test_a_rejected_key_is_not_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(401, content=b"nope"))

        with pytest.raises(WhatsAppAuthError):
            await provider.download_media(_credentials(), link=self.LINK, max_bytes=10)

    async def test_a_gateway_failure_is_retryable(self) -> None:
        provider, _ = _provider(httpx.Response(503, content=b"down"))

        with pytest.raises(WhatsAppTransientError):
            await provider.download_media(_credentials(), link=self.LINK, max_bytes=10)
