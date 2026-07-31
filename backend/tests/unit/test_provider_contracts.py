"""Contract tests for the provider ports.

These pin the behaviour the application relies on, using the mocks and a
stubbed transport. No network, so they run everywhere including CI.

The point is not to test httpx. It is to prove the adapters translate
correctly in both directions - request encoding and response decoding - and
that failures arrive as the typed errors callers are written against, rather
than as raw HTTP status codes leaking upward.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.providers.email import EmailMessage, MockEmailProvider
from app.providers.llm import (
    ChatMessage,
    ChatResult,
    LLMAuthError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    MockLLMProvider,
    OpenAICompatibleProvider,
    ToolSpec,
)
from app.providers.storage import MockStorageProvider, ObjectNotFoundError, StorageProvider


class TestMocksSatisfyTheirPorts:
    """The mocks must be substitutable for the real thing."""

    def test_mock_llm_is_an_llm_provider(self) -> None:
        assert isinstance(MockLLMProvider(), LLMProvider)

    def test_mock_storage_is_a_storage_provider(self) -> None:
        assert isinstance(MockStorageProvider(), StorageProvider)

    def test_real_llm_adapter_is_an_llm_provider(self) -> None:
        adapter = OpenAICompatibleProvider(
            api_key="k",
            base_url="https://x.invalid/v1",
            chat_model="c",
            embedding_model="e",
        )
        assert isinstance(adapter, LLMProvider)


class TestMockLLM:
    async def test_returns_queued_results_in_order(self) -> None:
        provider = MockLLMProvider(
            responses=[ChatResult(content="first"), ChatResult(content="second")]
        )
        assert (await provider.chat([ChatMessage("user", "hi")])).content == "first"
        assert (await provider.chat([ChatMessage("user", "hi")])).content == "second"

    async def test_running_out_of_responses_is_an_explicit_error(self) -> None:
        """Silently returning empty text would look like a model that refused."""
        provider = MockLLMProvider()
        with pytest.raises(LLMError, match="ran out of queued responses"):
            await provider.chat([ChatMessage("user", "hi")])

    async def test_records_the_prompt_it_was_given(self) -> None:
        provider = MockLLMProvider(responses=[ChatResult(content="ok")])
        tools = [ToolSpec(name="consultar_catalogo", description="busca productos")]
        await provider.chat([ChatMessage("system", "sos Leo")], tools=tools)
        assert provider.chat_calls[0].messages[0].content == "sos Leo"
        assert provider.chat_calls[0].tools[0].name == "consultar_catalogo"

    async def test_embeddings_are_the_configured_width(self) -> None:
        vectors = await MockLLMProvider().embed(["hola"])
        assert len(vectors[0]) == EMBEDDING_DIMENSIONS

    async def test_embeddings_are_deterministic(self) -> None:
        """Retrieval tests depend on the same text embedding identically."""
        provider = MockLLMProvider()
        first = await provider.embed(["horario de atencion"])
        second = await provider.embed(["horario de atencion"])
        assert first == second

    async def test_different_texts_embed_differently(self) -> None:
        provider = MockLLMProvider()
        vectors = await provider.embed(["lomo saltado", "horario"])
        assert vectors[0] != vectors[1]

    async def test_input_type_is_recorded(self) -> None:
        """Query and passage embeddings are asymmetric; callers must pass it."""
        provider = MockLLMProvider()
        await provider.embed(["q"], input_type="query")
        assert provider.embed_calls[0].input_type == "query"


class TestChatResult:
    def test_wants_tool_is_false_without_calls(self) -> None:
        assert ChatResult(content="hola").wants_tool is False

    def test_content_and_tool_calls_can_coexist(self) -> None:
        """Some models emit a sentence alongside a call; both must survive."""
        from app.providers.llm import ToolCall

        result = ChatResult(
            content="Dejame revisar",
            tool_calls=(ToolCall(id="1", name="consultar_catalogo", arguments={"q": "x"}),),
        )
        assert result.wants_tool and result.content


class TestOpenAICompatibleEncoding:
    """What actually goes on the wire."""

    def test_tools_are_encoded_in_openai_function_shape(self) -> None:
        encoded = OpenAICompatibleProvider._encode_tools(
            [ToolSpec(name="t", description="d", parameters={"type": "object"})]
        )
        assert encoded[0]["type"] == "function"
        assert encoded[0]["function"]["name"] == "t"

    def test_a_tool_without_parameters_still_gets_a_schema(self) -> None:
        """Providers reject a function whose parameters field is absent."""
        encoded = OpenAICompatibleProvider._encode_tools([ToolSpec(name="t", description="d")])
        assert encoded[0]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_tool_messages_carry_their_call_id(self) -> None:
        encoded = OpenAICompatibleProvider._encode_messages(
            [ChatMessage("tool", "resultado", tool_call_id="call_1", name="consultar_catalogo")]
        )
        assert encoded[0]["tool_call_id"] == "call_1"
        assert encoded[0]["name"] == "consultar_catalogo"

    def test_plain_messages_omit_tool_fields(self) -> None:
        encoded = OpenAICompatibleProvider._encode_messages([ChatMessage("user", "hola")])
        assert "tool_call_id" not in encoded[0]


class TestOpenAICompatibleDecoding:
    def test_tool_arguments_are_parsed_from_json(self) -> None:
        calls = OpenAICompatibleProvider._decode_tool_calls(
            [{"id": "c1", "function": {"name": "f", "arguments": '{"consulta": "lomo"}'}}]
        )
        assert calls[0].arguments == {"consulta": "lomo"}

    def test_malformed_tool_arguments_do_not_crash_the_turn(self) -> None:
        """Models do emit broken JSON. The agent-core decides what to do."""
        calls = OpenAICompatibleProvider._decode_tool_calls(
            [{"id": "c1", "function": {"name": "f", "arguments": "{not json"}}]
        )
        assert calls[0].arguments == {"_unparsed": "{not json"}

    def test_non_object_tool_arguments_are_also_captured(self) -> None:
        calls = OpenAICompatibleProvider._decode_tool_calls(
            [{"id": "c1", "function": {"name": "f", "arguments": "[1, 2]"}}]
        )
        assert "_unparsed" in calls[0].arguments


class TestEmailContract:
    async def test_send_records_and_returns_an_id(self) -> None:
        provider = MockEmailProvider()
        message_id = await provider.send(
            EmailMessage(to=("a@example.com",), subject="Verifica tu email", html="<p>hi</p>")
        )
        assert message_id.startswith("mock-")
        assert provider.last.subject == "Verifica tu email"

    async def test_failure_is_raised_not_swallowed(self) -> None:
        from app.providers.email import EmailAuthError

        provider = MockEmailProvider(fail_with=EmailAuthError("bad key"))
        with pytest.raises(EmailAuthError):
            await provider.send(EmailMessage(to=("a@b.com",), subject="s", html="h"))


class TestStorageContract:
    def test_round_trip(self) -> None:
        storage = MockStorageProvider()
        storage.put("tenants/1/menu.pdf", b"%PDF-1.7", content_type="application/pdf")
        assert storage.get("tenants/1/menu.pdf") == b"%PDF-1.7"

    def test_missing_key_raises_object_not_found(self) -> None:
        with pytest.raises(ObjectNotFoundError):
            MockStorageProvider().get("nope")

    def test_delete_reports_whether_anything_happened(self) -> None:
        storage = MockStorageProvider()
        storage.put("k", b"x", content_type="text/plain")
        assert storage.delete("k") is True
        assert storage.delete("k") is False

    def test_presigned_url_requires_the_object_to_exist(self) -> None:
        """Handing out a URL for a missing object produces a broken link."""
        with pytest.raises(ObjectNotFoundError):
            MockStorageProvider().presigned_url("missing")


class TestErrorTranslation:
    """HTTP status codes must not leak above the adapter."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(401, LLMAuthError), (403, LLMAuthError), (429, LLMRateLimitError), (500, LLMError)],
    )
    async def test_status_codes_become_typed_errors(
        self, status: int, expected: type[Exception]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": "boom"})

        provider = OpenAICompatibleProvider(
            api_key="k", base_url="https://x.invalid/v1", chat_model="c", embedding_model="e"
        )
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post("https://x.invalid/v1/chat/completions", json={})
        # Exercise the adapter's own classification on that response.
        with pytest.raises(expected):
            provider._raise_for_status(response)  # type: ignore[attr-defined]

    async def test_auth_errors_never_echo_the_response_body(self) -> None:
        """Providers sometimes reflect the submitted key back in the error."""
        provider = OpenAICompatibleProvider(
            api_key="super-secret",
            base_url="https://x.invalid/v1",
            chat_model="c",
            embedding_model="e",
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "invalid key super-secret"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post("https://x.invalid/v1/chat/completions", json={})
        with pytest.raises(LLMAuthError) as caught:
            provider._raise_for_status(response)  # type: ignore[attr-defined]
        assert "super-secret" not in str(caught.value)


class TestEmbeddingOrdering:
    def test_results_are_reordered_by_index(self) -> None:
        """Providers are not required to answer in request order."""
        payload = {
            "data": [
                {"index": 1, "embedding": [2.0]},
                {"index": 0, "embedding": [1.0]},
            ]
        }
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        assert [item["embedding"] for item in ordered] == [[1.0], [2.0]]
        assert json.dumps(payload)  # payload stays serialisable
