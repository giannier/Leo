"""Templates through YCloud (Feature 011, TASK-1102): submit, send, read the review."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.providers.whatsapp import (
    NumberCredentials,
    Recipient,
    TemplateSpec,
    TemplateStatusUpdate,
    WhatsAppError,
    YCloudProvider,
)

SENDER = "+51912345678"
CREDENTIALS = NumberCredentials(
    provider="ycloud",
    values={
        "waba_id": "102290129340398",
        "phone_number_id": "112233445566",
        "api_key": "yc_live_k1abcdef",
    },
)


def _provider(response: httpx.Response) -> tuple[YCloudProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return YCloudProvider(transport=httpx.MockTransport(handler)), seen


def _json(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


class TestSubmit:
    async def test_the_template_goes_with_its_examples_and_category(self) -> None:
        provider, seen = _provider(
            httpx.Response(200, json={"status": "PENDING", "officialTemplateId": "9001"})
        )
        result = await provider.submit_template(
            CREDENTIALS,
            TemplateSpec(
                name="promo_julio",
                language="es",
                category="marketing",
                body="Hola {{1}}, en {{2}} tenemos 2x1 esta semana.",
                examples=("Ana", "Panaderia Rosa"),
                footer="Escribe BAJA para no recibir promociones",
                header_image_link="https://cdn.example/promo.jpg",
            ),
        )
        assert result.status == "pending" and result.provider_template_id == "9001"
        request = seen[0]
        assert str(request.url) == "https://api.ycloud.com/v2/whatsapp/templates"
        assert request.headers["x-api-key"] == "yc_live_k1abcdef"
        assert _json(request) == {
            "wabaId": "102290129340398",
            "name": "promo_julio",
            "language": "es",
            "category": "MARKETING",
            "components": [
                {
                    "type": "HEADER",
                    "format": "IMAGE",
                    "example": {"header_url": ["https://cdn.example/promo.jpg"]},
                },
                {
                    "type": "BODY",
                    "text": "Hola {{1}}, en {{2}} tenemos 2x1 esta semana.",
                    "example": {"body_text": [["Ana", "Panaderia Rosa"]]},
                },
                {"type": "FOOTER", "text": "Escribe BAJA para no recibir promociones"},
            ],
        }

    async def test_a_refusal_is_an_error_not_a_status(self) -> None:
        provider, _ = _provider(httpx.Response(400, json={"error": "name already exists"}))
        with pytest.raises(WhatsAppError) as caught:
            await provider.submit_template(
                CREDENTIALS,
                TemplateSpec(name="x", language="es", category="utility", body="Hola"),
            )
        assert not caught.value.retryable


class TestSend:
    async def test_the_values_fill_the_body_in_order(self) -> None:
        provider, seen = _provider(httpx.Response(200, json={"id": "wamid.1", "status": "sent"}))
        result = await provider.send_template(
            CREDENTIALS,
            sender=SENDER,
            to=Recipient(phone="+51987654321"),
            name="promo_julio",
            language="es",
            values=["Ana", "Panaderia Rosa"],
            header_image_link="https://cdn.example/promo.jpg",
        )
        assert result.provider_message_id == "wamid.1"
        assert _json(seen[0]) == {
            "from": SENDER,
            "to": "+51987654321",
            "type": "template",
            "template": {
                "name": "promo_julio",
                "language": {"code": "es"},
                "components": [
                    {
                        "type": "header",
                        "parameters": [
                            {"type": "image", "image": {"link": "https://cdn.example/promo.jpg"}}
                        ],
                    },
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": "Ana"},
                            {"type": "text", "text": "Panaderia Rosa"},
                        ],
                    },
                ],
            },
        }

    async def test_a_template_without_variables_has_no_components(self) -> None:
        provider, seen = _provider(httpx.Response(200, json={"id": "wamid.2"}))
        await provider.send_template(
            CREDENTIALS,
            sender=SENDER,
            to=Recipient(phone="+51987654321"),
            name="aviso",
            language="es",
            values=[],
        )
        assert _json(seen[0])["template"] == {"name": "aviso", "language": {"code": "es"}}


class TestReview:
    @pytest.mark.parametrize(
        ("raw", "ours"),
        [
            ("APPROVED", "approved"),
            ("REJECTED", "rejected"),
            ("PAUSED", "paused"),
            ("DISABLED", "disabled"),
            ("IN_APPEAL", "pending"),
            ("SOMETHING_NEW", "pending"),
        ],
    )
    def test_meta_states_map_onto_ours(self, raw: str, ours: str) -> None:
        parsed = YCloudProvider().parse_event(
            {
                "id": "evt_1",
                "type": "whatsapp.template.reviewed",
                "whatsappTemplate": {
                    "wabaId": "102290129340398",
                    "name": "promo_julio",
                    "language": "es",
                    "status": raw,
                    "reason": "NONE",
                    "officialTemplateId": "9001",
                },
            }
        )
        assert parsed == TemplateStatusUpdate(
            waba_id="102290129340398",
            name="promo_julio",
            language="es",
            status=ours,
            reason=None,
            provider_template_id="9001",
        )

    def test_the_rejection_reason_comes_through(self) -> None:
        parsed = YCloudProvider().parse_event(
            {
                "id": "evt_2",
                "type": "whatsapp.template.reviewed",
                "whatsappTemplate": {
                    "wabaId": "1",
                    "name": "promo",
                    "language": "es",
                    "status": "REJECTED",
                    "reason": "INVALID_FORMAT",
                },
            }
        )
        assert isinstance(parsed, TemplateStatusUpdate) and parsed.reason == "INVALID_FORMAT"

    def test_an_incomplete_review_is_ignored(self) -> None:
        parsed = YCloudProvider().parse_event(
            {"id": "evt_3", "type": "whatsapp.template.reviewed", "whatsappTemplate": {"name": "x"}}
        )
        assert parsed is None
