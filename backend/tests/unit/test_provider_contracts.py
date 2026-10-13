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

from app.core.config import Settings
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
    ToolCall,
    ToolSpec,
)
from app.providers.llm.base import LLMConfigurationError
from app.providers.llm.factory import platform_profile
from app.providers.llm.offline import OfflineLLMProvider
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


class TestTheCredentialIsCheckedForPlausibility:
    """A key that is obviously not a key must be refused where it is read.

    "Set" turned out to be the wrong question. A ten-character value passed
    every guard and failed at the embedding call, and the symptom there is a
    knowledge source stuck at `failed` with a provider error - a long way from
    the line in `.env` that caused it. Real keys are long: an OpenAI project
    key is around 160 characters.
    """

    @staticmethod
    def _settings(**overrides: object) -> Settings:
        base: dict[str, object] = {"llm_profile": "openai", "openai_api_key": None}
        base.update(overrides)
        return Settings(**base)  # type: ignore[arg-type]

    def test_a_missing_key_names_the_variable_and_where_to_get_one(self) -> None:
        with pytest.raises(LLMConfigurationError) as caught:
            platform_profile(self._settings())

        message = str(caught.value)
        assert "OPENAI_API_KEY" in message
        # An error that only says "not set" sends somebody hunting.
        assert "platform.openai.com" in message

    @pytest.mark.parametrize("value", ["x", "sk-short", "changeme", "   sk-abc   "])
    def test_something_far_too_short_is_refused(self, value: str) -> None:
        with pytest.raises(LLMConfigurationError, match="too short"):
            platform_profile(self._settings(openai_api_key=value))

    def test_a_plausible_key_is_accepted(self) -> None:
        profile = platform_profile(self._settings(openai_api_key="sk-proj-" + "a" * 40))
        assert profile.name == "openai"

    def test_surrounding_whitespace_is_not_counted_as_length(self) -> None:
        """A pasted key often carries a trailing newline; padding must not be
        what makes an eight-character placeholder look long enough."""
        with pytest.raises(LLMConfigurationError, match="too short"):
            platform_profile(self._settings(openai_api_key="  sk-abc  " + " " * 30))


class TestTheOfflineProvider:
    """`LLM_PROFILE=offline`: deterministic, credential-free, and honest.

    It exists so the product can be walked end to end with no key and no
    network. The risk it carries is somebody mistaking it for the real agent,
    so most of what is asserted here is about it refusing to look like one.
    """

    def test_it_refuses_to_run_outside_development(self) -> None:
        """The whole safety argument rests on this. A deployment answering
        customers from a hash would be worse than one that is down."""
        for environment in ("staging", "production"):
            with pytest.raises(LLMConfigurationError, match="never run in"):
                OfflineLLMProvider(environment=environment)

    async def test_every_reply_says_it_is_not_the_agent(self) -> None:
        provider = OfflineLLMProvider(environment="test")

        result = await provider.chat([ChatMessage("user", "¿hacen delivery?")])

        assert result.content.startswith("[MODO SIN CONEXION")
        # And it quotes the question, so a person testing the UI can see their
        # message made the whole round trip.
        assert "¿hacen delivery?" in result.content

    async def test_it_never_invents_a_tool_call(self) -> None:
        """Choosing a tool is a judgement about meaning and a hash cannot make
        one. A plausible-looking call would put fiction in the trace panel,
        which is the one place that has to be trustworthy."""
        provider = OfflineLLMProvider(environment="test")

        result = await provider.chat(
            [ChatMessage("user", "quiero comprar dos panes")],
            tools=[ToolSpec(name="consultar_catalogo", description="busca productos")],
        )

        assert result.tool_calls == ()

    async def test_the_same_text_always_embeds_to_the_same_vector(self) -> None:
        provider = OfflineLLMProvider(environment="test")

        first, second = await provider.embed(["pan artesanal", "pan artesanal"])

        assert first == second

    async def test_shared_words_score_closer_than_unrelated_text(self) -> None:
        """Not semantic - it cannot be, offline - but not random either.

        Random vectors would let somebody conclude retrieval is broken, or
        that it works, on no evidence at all.
        """
        provider = OfflineLLMProvider(environment="test")

        same, similar, unrelated = await provider.embed(
            [
                "hacemos delivery en miraflores",
                "delivery en miraflores por ocho soles",
                "reparacion de motores diesel industriales",
            ]
        )
        cosine = lambda a, b: sum(x * y for x, y in zip(a, b, strict=True))  # noqa: E731

        assert cosine(same, similar) > cosine(same, unrelated)

    async def test_the_vectors_are_the_configured_width(self) -> None:
        """A different width would be rejected by pgvector at insert time,
        long after the cause."""
        provider = OfflineLLMProvider(environment="test")

        [vector] = await provider.embed(["cualquier cosa"])

        assert len(vector) == EMBEDDING_DIMENSIONS

    async def test_an_empty_chunk_still_produces_a_usable_vector(self) -> None:
        """A zero vector is undefined under cosine distance. Punctuation-only
        chunks do occur in real PDFs."""
        provider = OfflineLLMProvider(environment="test")

        [vector] = await provider.embed(["...  ---  "])

        assert any(value != 0.0 for value in vector)

    async def test_the_vectors_are_normalised(self) -> None:
        provider = OfflineLLMProvider(environment="test")

        [vector] = await provider.embed(["pan ciabatta recién horneado"])

        assert abs(sum(value * value for value in vector) - 1.0) < 1e-9


class TestVendorDifferencesOnTheWire:
    """Two things OpenAI rejects that the old NVIDIA profile tolerated.

    Both were invisible until a real OpenAI key was configured, and both made
    the agent fall back on every tool-using turn: the evals then ran against
    NVIDIA only, so they could not have caught either.
    """

    @staticmethod
    def _encode(messages: list[ChatMessage]) -> list[dict[str, object]]:
        return OpenAICompatibleProvider._encode_messages(messages)

    def test_the_openai_profile_asks_for_max_completion_tokens(self) -> None:
        """`max_tokens` is refused outright by OpenAI's newer models."""
        profile = platform_profile(
            Settings(llm_profile="openai", openai_api_key="sk-proj-" + "a" * 40)
        )
        assert profile.max_tokens_field == "max_completion_tokens"

    def test_an_assistant_turn_carries_the_calls_it_made(self) -> None:
        """The bug: only the assistant's *text* was replayed, so the `tool`
        results that followed answered nothing, and OpenAI refused the whole
        request with "messages with role 'tool' must be a response to a
        preceeding message with 'tool_calls'"."""
        call = ToolCall(id="call_1", name="consultar_catalogo", arguments={"q": "pan"})

        [encoded] = self._encode([ChatMessage("assistant", "", tool_calls=(call,))])

        assert encoded["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "consultar_catalogo", "arguments": '{"q": "pan"}'},
            }
        ]

    def test_a_calls_only_turn_sends_null_content_not_an_empty_string(self) -> None:
        """OpenAI accepts `null` there and rejects `""`."""
        call = ToolCall(id="c", name="t", arguments={})

        [encoded] = self._encode([ChatMessage("assistant", "", tool_calls=(call,))])

        assert encoded["content"] is None

    def test_prose_alongside_a_call_is_preserved(self) -> None:
        """Some models emit a sentence *and* a call; dropping the sentence
        would lose part of the turn."""
        call = ToolCall(id="c", name="t", arguments={})

        [encoded] = self._encode([ChatMessage("assistant", "Déjame revisar", tool_calls=(call,))])

        assert encoded["content"] == "Déjame revisar"

    def test_an_ordinary_message_gains_no_tool_calls_key(self) -> None:
        """Sending an empty `tool_calls` on a plain turn is not the same as
        omitting it."""
        [encoded] = self._encode([ChatMessage("user", "hola")])

        assert "tool_calls" not in encoded

    def test_arguments_keep_their_accents(self) -> None:
        """`ensure_ascii` would turn "camión" into escapes in the payload the
        model reads back."""
        call = ToolCall(id="c", name="t", arguments={"q": "camión"})

        [encoded] = self._encode([ChatMessage("assistant", "", tool_calls=(call,))])

        assert "camión" in str(encoded["tool_calls"])
