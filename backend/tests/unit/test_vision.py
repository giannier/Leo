"""Reading a receipt photo (Feature 004, TASK-429): the parts that need no database.

The model is `MockLLMProvider`: nothing here calls a real vision model. What is
asserted is what `read_proof` does with what a model says, including when the model
says nothing at all (a timeout, an error, no vision on that provider).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.orders.vision import SCHEMA, parse_reading, read_proof
from app.providers.llm import MockLLMProvider
from app.providers.llm.base import LLMError, VisionProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider

D = Decimal
PHOTO = b"\xff\xd8\xff fake jpeg"


def receipt(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "es_comprobante": True,
        "monto": "500.00",
        "moneda": "PEN",
        "fecha": "2026-10-12",
        "codigo": "OP-123",
        "entidad": "BCP",
        "destinatario": "Escuela Capacita SAC",
        "confianza": 0.93,
    }
    data.update(overrides)
    return data


class TestParsing:
    def test_a_receipt_is_read_into_typed_fields(self) -> None:
        reading = parse_reading(receipt(), model="m")

        assert reading.legible and reading.es_comprobante
        assert reading.monto == D("500.00")
        assert reading.moneda == "PEN"
        assert reading.fecha == date(2026, 10, 12)
        assert reading.codigo == "OP-123"
        assert reading.confianza == D("0.93")
        assert reading.has_data

    def test_a_photo_that_is_not_a_receipt_carries_no_data(self) -> None:
        reading = parse_reading({"es_comprobante": False})

        assert reading.legible and not reading.es_comprobante
        assert reading.monto is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("S/ 1,250.50", D("1250.50")),
            ("1250.5", D("1250.50")),
            ("120,50", D("120.50")),
            ("1.250", D("1250.00")),
            ("1,250", D("1250.00")),
            ("1.250,00", D("1250.00")),
            ("S/ 1.250,5", D("1250.50")),
            ("1,250.00", D("1250.00")),
            ("1.250.000", D("1250000.00")),
            ("1,250,000.75", D("1250000.75")),
            ("120.5", D("120.50")),
            ("S/ 0", D("0.00")),
            ("diez soles", None),
            ("-5", D("-5.00")),
            ("S/ -120.00", D("-120.00")),
            ("\u2212 120,00", D("-120.00")),
            ("-0", D("0.00")),
            (None, None),
            ("99999999999", None),
        ],
    )
    def test_amounts_are_decimal_never_float(
        self, raw: str | None, expected: Decimal | None
    ) -> None:
        reading = parse_reading(receipt(monto=raw))

        assert reading.monto == expected
        assert reading.monto is None or isinstance(reading.monto, Decimal)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("S/", "PEN"), ("pen", "PEN"), ("US$", "USD"), ("soles peruanos", None), (None, None)],
    )
    def test_the_currency_is_an_iso_code_or_nothing(
        self, raw: str | None, expected: str | None
    ) -> None:
        assert parse_reading(receipt(moneda=raw)).moneda == expected

    @pytest.mark.parametrize("raw", ["ayer", "2026-13-45", None, 5])
    def test_a_date_that_does_not_parse_is_dropped(self, raw: object) -> None:
        assert parse_reading(receipt(fecha=raw)).fecha is None

    @pytest.mark.parametrize("raw", [1.4, -0.1, "alta", True, None])
    def test_a_confidence_outside_zero_to_one_is_dropped(self, raw: object) -> None:
        assert parse_reading(receipt(confianza=raw)).confianza is None

    def test_an_answer_without_the_verdict_is_unreadable(self) -> None:
        reading = parse_reading({"monto": "10"})

        assert not reading.legible
        assert reading.reason == "respuesta_invalida"


class TestReadProof:
    async def test_it_sends_the_image_and_the_schema_to_the_provider(self) -> None:
        llm = MockLLMProvider()
        llm.vision_responses.append(receipt())

        reading = await read_proof(llm, PHOTO, "image/jpeg")

        assert reading.es_comprobante
        call = llm.vision_calls[0]
        assert call.image == PHOTO and call.mime_type == "image/jpeg"
        assert call.schema == SCHEMA
        assert call.timeout_seconds == 8.0

    async def test_a_slow_model_times_out_and_the_turn_goes_on(self) -> None:
        llm = MockLLMProvider()
        llm.vision_delay = 0.5
        llm.vision_responses.append(receipt())

        reading = await read_proof(llm, PHOTO, "image/jpeg", timeout_seconds=0.05)

        assert not reading.legible
        assert reading.reason == "timeout"

    async def test_a_provider_error_is_unreadable_not_an_exception(self) -> None:
        llm = MockLLMProvider()
        llm.vision_responses.append(LLMError("boom"))

        reading = await read_proof(llm, PHOTO, "image/jpeg")

        assert not reading.legible
        assert reading.reason == "error"

    async def test_a_model_that_cannot_see_is_unreadable(self) -> None:
        class ChatOnly:
            chat_model = "x"
            embedding_model = "y"

        assert not isinstance(ChatOnly(), VisionProvider)

        reading = await read_proof(ChatOnly(), PHOTO, "image/jpeg")  # type: ignore[arg-type]

        assert reading.reason == "sin_vision"


class TestOpenAIAdapter:
    """The wire format, against a transport that never leaves the process."""

    async def test_the_image_travels_inline_with_a_json_schema(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            seen["timeout"] = request.extensions["timeout"]
            answer = {
                "model": "vision-1",
                "choices": [{"message": {"content": json.dumps(receipt())}}],
                "usage": {"prompt_tokens": 800, "completion_tokens": 60},
            }
            return httpx.Response(200, json=answer)

        provider = OpenAICompatibleProvider(
            api_key="k" * 30,
            base_url="https://llm.test/v1",
            chat_model="chat-1",
            embedding_model="emb-1",
            vision_model="vision-1",
            max_tokens_field="max_completion_tokens",
            transport=httpx.MockTransport(handler),
        )

        reading = await provider.read_image(
            PHOTO, "image/jpeg", instruction="read", schema=SCHEMA, timeout_seconds=8.0
        )

        body = seen["body"]
        assert body["model"] == "vision-1"
        assert body["max_completion_tokens"] == 400
        assert body["response_format"]["json_schema"]["schema"] == SCHEMA
        content = body["messages"][0]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert seen["timeout"]["read"] == 8.0
        assert reading.data["codigo"] == "OP-123"
        assert (reading.input_tokens, reading.output_tokens) == (800, 60)

    async def test_an_answer_that_is_not_json_is_an_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "no sé"}}]})

        provider = OpenAICompatibleProvider(
            api_key="k" * 30,
            base_url="https://llm.test/v1",
            chat_model="chat-1",
            embedding_model="emb-1",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(LLMError):
            await provider.read_image(
                PHOTO, "image/png", instruction="read", schema=SCHEMA, timeout_seconds=8.0
            )


class TestOpenAIDocumentReading:
    """Feature 006: a scanned PDF page travels as a file part, with its own token cap."""

    async def test_the_page_travels_inline_as_a_pdf_file(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            answer = {
                "model": "vision-1",
                "choices": [{"message": {"content": json.dumps({"legible": True, "texto": "x"})}}],
                "usage": {"prompt_tokens": 1100, "completion_tokens": 90},
            }
            return httpx.Response(200, json=answer)

        provider = OpenAICompatibleProvider(
            api_key="k" * 30,
            base_url="https://llm.test/v1",
            chat_model="chat-1",
            embedding_model="emb-1",
            vision_model="vision-1",
            max_tokens_field="max_completion_tokens",
            transport=httpx.MockTransport(handler),
        )

        reading = await provider.read_document(
            b"%PDF-1.4 page",
            instruction="transcribe",
            schema={"type": "object"},
            timeout_seconds=90.0,
            max_tokens=2500,
        )

        body = seen["body"]
        assert body["model"] == "vision-1" and body["max_completion_tokens"] == 2500
        part = body["messages"][0]["content"][1]
        assert part["type"] == "file"
        assert part["file"]["file_data"].startswith("data:application/pdf;base64,")
        assert reading.data == {"legible": True, "texto": "x"}
        assert (reading.input_tokens, reading.output_tokens) == (1100, 90)
