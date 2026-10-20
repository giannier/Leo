"""Connecting a business's own YCloud account: finding its number and creating its webhook."""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.whatsapp import WhatsAppAuthError, YCloudProvider
from app.providers.whatsapp.base import NumberNotInAccount, WhatsAppError

KEY = "ycloud_key_0123456789"


def _provider(handler: httpx.MockTransport) -> YCloudProvider:
    return YCloudProvider(transport=handler)


def _numbers(*phones: str) -> dict[str, object]:
    return {
        "items": [
            {"phoneNumber": phone, "wabaId": f"waba_{i}", "id": f"pn_{i}_0123"}
            for i, phone in enumerate(phones)
        ],
        "total": len(phones),
    }


class TestDiscoverNumber:
    async def test_finds_the_number_and_its_waba(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_numbers("+51900000001", "+51 999 888 777"))

        found = await _provider(httpx.MockTransport(handler)).discover_number(
            api_key=KEY, phone_e164="+51999888777"
        )

        assert found.waba_id == "waba_1"
        assert found.phone_number_id == "pn_1_0123"
        assert seen[0].method == "GET"
        assert seen[0].headers["X-API-Key"] == KEY

    async def test_a_number_the_account_does_not_hold_is_refused(self) -> None:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json=_numbers("+51900000001"))
        )

        with pytest.raises(NumberNotInAccount):
            await _provider(transport).discover_number(api_key=KEY, phone_e164="+51999888777")

    async def test_a_rejected_key_is_an_auth_error(self) -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(401, json={}))

        with pytest.raises(WhatsAppAuthError):
            await _provider(transport).discover_number(api_key=KEY, phone_e164="+51999888777")


class TestRegisterWebhook:
    async def test_creates_the_endpoint_and_returns_its_secret(self) -> None:
        bodies: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "we_1", "secret": "whsec_abc"})

        registered = await _provider(httpx.MockTransport(handler)).register_webhook(
            api_key=KEY, url="https://leo.example/webhooks/ycloud/tok"
        )

        assert (registered.endpoint_id, registered.secret) == ("we_1", "whsec_abc")
        assert bodies[0]["url"] == "https://leo.example/webhooks/ycloud/tok"
        assert "whatsapp.inbound_message.received" in bodies[0]["enabledEvents"]  # type: ignore[operator]

    async def test_retries_with_the_required_events_if_a_name_is_refused(self) -> None:
        sent: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            events = json.loads(request.content)["enabledEvents"]
            sent.append(events)
            if len(sent) == 1:
                return httpx.Response(400, json={"message": "unknown event"})
            return httpx.Response(200, json={"id": "we_1", "secret": "whsec_abc"})

        registered = await _provider(httpx.MockTransport(handler)).register_webhook(
            api_key=KEY, url="https://leo.example/webhooks/ycloud/tok"
        )

        assert registered.secret == "whsec_abc"
        assert len(sent) == 2 and len(sent[1]) < len(sent[0])

    async def test_a_response_without_a_secret_is_an_error(self) -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "we_1"}))

        with pytest.raises(WhatsAppError):
            await _provider(transport).register_webhook(api_key=KEY, url="https://x/y")

    async def test_a_bad_key_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(403, json={})

        with pytest.raises(WhatsAppAuthError):
            await _provider(httpx.MockTransport(handler)).register_webhook(
                api_key=KEY, url="https://x/y"
            )
        assert calls == 1


class TestRemoveWebhook:
    async def test_is_best_effort(self) -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(500, json={}))

        await _provider(transport).remove_webhook(api_key=KEY, endpoint_id="we_1")
