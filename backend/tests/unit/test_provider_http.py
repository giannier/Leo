"""The HTTP adapters, driven end to end against a stubbed transport.

`test_provider_contracts.py` covers the ports and the pure encoders. What was
left untested is the part in between: the request each adapter actually
assembles, and what it does with a real `httpx.Response`. That is where the
vendor differences live - `max_completion_tokens` versus `max_tokens`, the
`input_type` field NVIDIA requires and OpenAI rejects - and getting one of
them wrong fails at run time against a paid API, which is the worst place to
find out.

No network. Every test hands the adapter an `httpx.MockTransport`, so the
request can be inspected and the response dictated.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.providers.email.base import EmailAuthError, EmailError, EmailMessage
from app.providers.email.resend_provider import RESEND_URL, ResendEmailProvider
from app.providers.llm.base import ChatMessage, LLMError, ToolSpec
from app.providers.llm.openai_compatible import OpenAICompatibleProvider

CHAT_BODY: dict[str, Any] = {
    "model": "served-model-name",
    "choices": [
        {
            "message": {"role": "assistant", "content": "Buenas, en que te ayudo?"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 31, "completion_tokens": 9},
}


def _recorder(response: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A transport that answers `response` and keeps what it was sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return httpx.MockTransport(handler), seen


def _llm(**overrides: Any) -> tuple[OpenAICompatibleProvider, list[httpx.Request]]:
    response = overrides.pop("response", httpx.Response(200, json=CHAT_BODY))
    transport, seen = _recorder(response)
    provider = OpenAICompatibleProvider(
        api_key="k",
        base_url="https://vendor.invalid/v1/",
        chat_model="a-chat-model",
        embedding_model="an-embedding-model",
        transport=transport,
        **overrides,
    )
    return provider, seen


class TestTheChatRequest:
    async def test_it_is_posted_to_the_vendors_completions_path(self) -> None:
        provider, seen = _llm()

        await provider.chat([ChatMessage(role="user", content="hola")])

        # The trailing slash in the configured base URL must not produce a
        # double slash: some gateways answer 404 for `//chat/completions`.
        assert str(seen[0].url) == "https://vendor.invalid/v1/chat/completions"

    async def test_the_credential_travels_as_a_bearer_token(self) -> None:
        provider, seen = _llm()

        await provider.chat([ChatMessage(role="user", content="hola")])

        assert seen[0].headers["authorization"] == "Bearer k"

    async def test_streaming_is_off_and_the_models_name_is_sent(self) -> None:
        provider, seen = _llm()

        await provider.chat([ChatMessage(role="user", content="hola")])

        payload = _json(seen[0])
        assert payload["stream"] is False
        assert payload["model"] == "a-chat-model"
        assert payload["messages"] == [{"role": "user", "content": "hola"}]

    async def test_the_output_cap_uses_the_field_this_vendor_accepts(self) -> None:
        """OpenAI's newer models reject `max_tokens` outright and demand
        `max_completion_tokens`; NVIDIA still takes the old name. Sending the
        wrong one is a 400 from a paid endpoint."""
        provider, seen = _llm(max_tokens_field="max_completion_tokens")

        await provider.chat([ChatMessage(role="user", content="hola")], max_tokens=256)

        payload = _json(seen[0])
        assert payload["max_completion_tokens"] == 256
        assert "max_tokens" not in payload

    async def test_tools_are_only_sent_when_there_are_some(self) -> None:
        """An empty `tools` array is not the same as no tools: some vendors
        reject it, and it changes how others behave."""
        provider, seen = _llm()

        await provider.chat([ChatMessage(role="user", content="hola")])
        assert "tools" not in _json(seen[0])

        await provider.chat(
            [ChatMessage(role="user", content="hola")],
            tools=[ToolSpec(name="consultar_catalogo", description="Busca productos")],
        )
        assert _json(seen[1])["tools"][0]["function"]["name"] == "consultar_catalogo"


class TestTheChatResponse:
    async def test_the_reply_and_its_accounting_are_read_back(self) -> None:
        provider, _ = _llm()

        result = await provider.chat([ChatMessage(role="user", content="hola")])

        assert result.content == "Buenas, en que te ayudo?"
        assert result.finish_reason == "stop"
        assert (result.input_tokens, result.output_tokens) == (31, 9)

    async def test_the_model_reported_is_the_one_that_answered(self) -> None:
        """Not the one that was asked for. A gateway may route to a different
        build, and the trace has to say which one produced the reply."""
        provider, _ = _llm()

        result = await provider.chat([ChatMessage(role="user", content="hola")])

        assert result.model == "served-model-name"

    async def test_latency_is_measured(self) -> None:
        provider, _ = _llm()

        result = await provider.chat([ChatMessage(role="user", content="hola")])

        assert result.latency_ms >= 0

    async def test_tool_calls_come_back_decoded(self) -> None:
        body = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "consultar_catalogo",
                                    "arguments": '{"consulta": "leche"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        provider, _ = _llm(response=httpx.Response(200, json=body))

        result = await provider.chat([ChatMessage(role="user", content="tienen leche?")])

        assert result.wants_tool is True
        assert result.tool_calls[0].name == "consultar_catalogo"
        assert result.tool_calls[0].arguments == {"consulta": "leche"}
        # A turn that only asked for a tool has no prose, and the empty string
        # is what the agent-core is written against - never `None`.
        assert result.content == ""

    async def test_an_answer_with_no_choices_is_an_error_not_an_empty_reply(self) -> None:
        """Returning `""` here would send a blank message to a customer."""
        provider, _ = _llm(response=httpx.Response(200, json={"choices": []}))

        with pytest.raises(LLMError, match="no choices"):
            await provider.chat([ChatMessage(role="user", content="hola")])


class TestTheEmbeddingRequest:
    async def test_nothing_to_embed_makes_no_request_at_all(self) -> None:
        provider, seen = _llm()

        assert await provider.embed([]) == []
        assert seen == []

    async def test_the_input_type_is_sent_where_the_vendor_requires_it(self) -> None:
        """NVIDIA rejects an embedding request without it; OpenAI rejects the
        unknown field. One adapter, one flag."""
        body = {"data": [{"index": 0, "embedding": [0.5]}]}
        provider, seen = _llm(response=httpx.Response(200, json=body))

        await provider.embed(["una frase"], input_type="query")

        assert _json(seen[0])["input_type"] == "query"

    async def test_it_is_withheld_where_the_vendor_rejects_it(self) -> None:
        body = {"data": [{"index": 0, "embedding": [0.5]}]}
        provider, seen = _llm(
            response=httpx.Response(200, json=body), send_embedding_input_type=False
        )

        await provider.embed(["una frase"])

        assert "input_type" not in _json(seen[0])

    async def test_the_width_is_requested_when_one_is_configured(self) -> None:
        """What lets the paid profile produce vectors the same width as the
        free one, so both share the `knowledge_chunks.embedding` column."""
        body = {"data": [{"index": 0, "embedding": [0.5]}]}
        provider, seen = _llm(response=httpx.Response(200, json=body), embedding_dimensions=1024)

        await provider.embed(["una frase"])

        assert _json(seen[0])["dimensions"] == 1024


class TestTheEmbeddingResponse:
    async def test_vectors_are_returned_in_the_order_they_were_asked_for(self) -> None:
        """Providers are not required to answer in request order, and pairing
        the wrong vector with the wrong chunk corrupts the index silently."""
        body = {
            "data": [
                {"index": 1, "embedding": [2.0]},
                {"index": 0, "embedding": [1.0]},
            ]
        }
        provider, _ = _llm(response=httpx.Response(200, json=body))

        assert await provider.embed(["primero", "segundo"]) == [[1.0], [2.0]]

    async def test_a_short_answer_is_refused(self) -> None:
        """Two texts in, one vector out. Zipping those together would attach
        the wrong embedding to a chunk for as long as the index lives."""
        body = {"data": [{"index": 0, "embedding": [1.0]}]}
        provider, _ = _llm(response=httpx.Response(200, json=body))

        with pytest.raises(LLMError, match="expected 2 embeddings"):
            await provider.embed(["primero", "segundo"])


class TestWhenTheVendorMisbehaves:
    async def test_a_timeout_names_the_budget_it_exceeded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        provider = OpenAICompatibleProvider(
            api_key="k",
            base_url="https://vendor.invalid/v1",
            chat_model="c",
            embedding_model="e",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(LLMError, match="timed out"):
            await provider.chat([ChatMessage(role="user", content="hola")])

    async def test_an_unreachable_host_is_an_llm_error_not_an_httpx_one(self) -> None:
        """Callers are written against `LLMError`. An `httpx` exception
        escaping the adapter would take down the turn instead of falling back
        to the canned reply."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        provider = OpenAICompatibleProvider(
            api_key="k",
            base_url="https://vendor.invalid/v1",
            chat_model="c",
            embedding_model="e",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(LLMError, match="could not reach"):
            await provider.chat([ChatMessage(role="user", content="hola")])

    async def test_html_where_json_was_promised_is_an_llm_error(self) -> None:
        """A proxy or a captive portal answers 200 with a login page."""
        provider, _ = _llm(response=httpx.Response(200, text="<html>maintenance</html>"))

        with pytest.raises(LLMError, match="non-JSON"):
            await provider.chat([ChatMessage(role="user", content="hola")])


class TestResend:
    @staticmethod
    def _provider(response: httpx.Response) -> tuple[ResendEmailProvider, list[httpx.Request]]:
        transport, seen = _recorder(response)
        return (
            ResendEmailProvider(
                api_key="re_k", sender="Leo <leo@example.com>", transport=transport
            ),
            seen,
        )

    async def test_a_sent_message_returns_the_providers_id(self) -> None:
        provider, _ = self._provider(httpx.Response(200, json={"id": "abc-123"}))

        message_id = await provider.send(
            EmailMessage(to=("ana@example.com",), subject="Hola", html="<p>Hola</p>")
        )

        assert message_id == "abc-123"

    async def test_the_payload_carries_what_resend_needs(self) -> None:
        provider, seen = self._provider(httpx.Response(200, json={"id": "abc-123"}))

        await provider.send(
            EmailMessage(
                to=("ana@example.com", "luis@example.com"),
                subject="Hola",
                html="<p>Hola</p>",
                text="Hola",
                reply_to="soporte@example.com",
            )
        )

        payload = _json(seen[0])
        assert str(seen[0].url) == RESEND_URL
        assert payload["from"] == "Leo <leo@example.com>"
        assert payload["to"] == ["ana@example.com", "luis@example.com"]
        assert payload["reply_to"] == "soporte@example.com"
        assert payload["text"] == "Hola"

    async def test_the_optional_fields_are_omitted_rather_than_sent_empty(self) -> None:
        provider, seen = self._provider(httpx.Response(200, json={"id": "abc-123"}))

        await provider.send(
            EmailMessage(to=("ana@example.com",), subject="Hola", html="<p>Hola</p>")
        )

        payload = _json(seen[0])
        assert "text" not in payload
        assert "reply_to" not in payload

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_key_is_its_own_error(self, status: int) -> None:
        """Retrying will not help, and the caller has to be able to tell that
        apart from a transient failure."""
        provider, _ = self._provider(httpx.Response(status, json={"message": "invalid key"}))

        with pytest.raises(EmailAuthError):
            await provider.send(
                EmailMessage(to=("ana@example.com",), subject="Hola", html="<p>Hola</p>")
            )

    async def test_any_other_failure_is_a_plain_email_error(self) -> None:
        provider, _ = self._provider(httpx.Response(500, text="upstream exploded"))

        with pytest.raises(EmailError) as caught:
            await provider.send(
                EmailMessage(to=("ana@example.com",), subject="Hola", html="<p>Hola</p>")
            )
        assert not isinstance(caught.value, EmailAuthError)

    async def test_an_accepted_request_with_no_id_is_not_treated_as_sent(self) -> None:
        """The id is the only handle there is for supporting a customer who
        says the email never arrived."""
        provider, _ = self._provider(httpx.Response(200, json={}))

        with pytest.raises(EmailError, match="no id"):
            await provider.send(
                EmailMessage(to=("ana@example.com",), subject="Hola", html="<p>Hola</p>")
            )

    async def test_an_unreachable_resend_is_an_email_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        provider = ResendEmailProvider(
            api_key="re_k",
            sender="Leo <leo@example.com>",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(EmailError, match="could not reach"):
            await provider.send(
                EmailMessage(to=("ana@example.com",), subject="Hola", html="<p>Hola</p>")
            )


class TestResendDefaultReplyTo:
    """A no-reply sender needs somewhere for answers to land."""

    async def _sent(
        self, provider_reply_to: str | None, message_reply_to: str | None
    ) -> dict[str, Any]:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(_json(request))
            return httpx.Response(200, json={"id": "em_1"})

        provider = ResendEmailProvider(
            api_key="re_k",
            sender="Leo <no-responder@mail.example.com>",
            reply_to=provider_reply_to,
            transport=httpx.MockTransport(handler),
        )
        await provider.send(
            EmailMessage(
                to=("ana@example.com",),
                subject="Hola",
                html="<p>Hola</p>",
                reply_to=message_reply_to,
            )
        )
        return seen[0]

    async def test_the_configured_address_is_used_when_the_message_has_none(self) -> None:
        body = await self._sent("soporte@example.com", None)
        assert body["reply_to"] == "soporte@example.com"

    async def test_a_message_s_own_address_wins(self) -> None:
        body = await self._sent("soporte@example.com", "ventas@example.com")
        assert body["reply_to"] == "ventas@example.com"

    async def test_without_either_nothing_is_sent(self) -> None:
        body = await self._sent(None, None)
        assert "reply_to" not in body


def _json(request: httpx.Request) -> dict[str, Any]:
    import json

    decoded: dict[str, Any] = json.loads(request.content)
    return decoded
