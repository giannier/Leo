"""One adapter for every OpenAI-compatible chat API.

OpenAI and the endpoints that copy its protocol (Azure OpenAI, gateways, the
`byo_openai_compatible` tenants) speak the same wire protocol, so they need one
implementation. Only the base URL, the credential, the model names and two
vendor quirks differ.

Deliberately built on plain `httpx` instead of the `openai` SDK: the payloads
are ordinary JSON, and a dependency whose only job is to build that JSON is
not worth the supply-chain surface. This module is the single place any of
this leaves the application (Constitution: third-party access lives in
`providers/`).
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.providers.llm.base import (
    ChatMessage,
    ChatResult,
    InputType,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    ToolCall,
    ToolSpec,
    VisionReading,
)

# 30 seconds, not 60. This bounds how long a WhatsApp customer stares at a
# typing indicator, and nobody waits a minute for a chat reply - by then the
# canned fallback is a better answer than a late correct one. Measured
# baseline against NVIDIA: ~2s without tools, ~8s with them, so 30s is a wide
# margin for a slow day rather than a tight budget.
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class OpenAICompatibleProvider:
    """Chat and embeddings against any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dimensions: int | None = None,
        send_embedding_input_type: bool = True,
        max_tokens_field: str = "max_tokens",
        vision_model: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        # OpenAI's v3 embedding models accept a `dimensions` parameter that
        # truncates the output. That is what lets the paid profile produce
        # vectors the same width as the free one, so both can share the
        # `knowledge_chunks.embedding` column instead of forcing a migration
        # and a full re-embedding whenever a tenant switches provider.
        self._embedding_dimensions = embedding_dimensions
        # Some compatible endpoints require `input_type`; OpenAI rejects it.
        self._send_embedding_input_type = send_embedding_input_type
        # What this vendor calls the output cap. OpenAI's newer models
        # reject `max_tokens` outright and demand
        # `max_completion_tokens`; some compatible endpoints still take
        # `max_tokens`. Same
        # kind of vendor difference as `send_embedding_input_type`, so it
        # is carried the same way rather than branched on a provider name.
        self._max_tokens_field = max_tokens_field
        # The model that reads images. It is the chat model unless told otherwise:
        # which model suits which intelligence level is TASK-445's decision.
        self._vision_model = vision_model or chat_model
        self._timeout = timeout
        # The seam that makes everything below testable without a network.
        # `None` is httpx's own default, so production behaviour is untouched;
        # the suite passes a `MockTransport` and gets to assert on the exact
        # payload this adapter builds. Without it the only reachable parts of
        # the class are the pure encoders, and the request assembly - which is
        # where the vendor differences live - could only be checked by calling
        # a paid API.
        self._transport = transport

    @property
    def chat_model(self) -> str:
        return self._chat_model

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    # ------------------------------------------------------------------ http

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Translate an HTTP status into the port's error vocabulary.

        Separate from `_post` so the mapping is unit-testable without a
        network round trip, and so no status code ever leaks to a caller
        that is written against `LLMError` and its subclasses.
        """
        if response.status_code in (401, 403):
            # The body is deliberately not included: providers sometimes echo
            # the submitted key back inside the error payload, and this
            # message ends up in logs.
            raise LLMAuthError(
                f"{self._base_url} rejected the credential (HTTP {response.status_code})"
            )
        if response.status_code == 429:
            raise LLMRateLimitError(f"{self._base_url} rate limited the request")
        if response.status_code >= 400:
            raise LLMError(
                f"HTTP {response.status_code} from {self._base_url}: {response.text[:200]}"
            )

    async def _post(
        self, path: str, payload: dict[str, Any], *, limit: httpx.Timeout | None = None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        limit = limit or self._timeout
        try:
            async with httpx.AsyncClient(timeout=limit, transport=self._transport) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(f"{self._base_url} timed out after {limit.read}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach {self._base_url}: {exc}") from exc

        self._raise_for_status(response)

        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise LLMError(f"{self._base_url} returned a non-JSON body") from exc
        return body

    # ------------------------------------------------------------------ chat

    @staticmethod
    def _encode_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        encoded: list[dict[str, Any]] = []
        for message in messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_call_id is not None:
                item["tool_call_id"] = message.tool_call_id
            if message.name is not None:
                item["name"] = message.name
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
                # An assistant turn that only asked for tools has no prose.
                # OpenAI accepts null there but not the empty string.
                if not message.content:
                    item["content"] = None
            encoded.append(item)
        return encoded

    @staticmethod
    def _encode_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _decode_tool_calls(raw_calls: list[dict[str, Any]]) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for raw in raw_calls:
            function = raw.get("function", {})
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args)
            except (ValueError, TypeError):
                # Models do occasionally emit malformed JSON. Surfacing the
                # raw text lets the agent-core decide (retry, or answer
                # without the tool) instead of the adapter crashing the turn.
                arguments = {"_unparsed": raw_args}
            if not isinstance(arguments, dict):
                arguments = {"_unparsed": raw_args}
            calls.append(
                ToolCall(
                    id=raw.get("id") or "",
                    name=function.get("name") or "",
                    arguments=arguments,
                )
            )
        return tuple(calls)

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "messages": self._encode_messages(messages),
            "temperature": temperature,
            self._max_tokens_field: max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = self._encode_tools(tools)

        started = time.perf_counter()
        body = await self._post("/chat/completions", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        choices = body.get("choices") or []
        if not choices:
            raise LLMError("provider returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}

        return ChatResult(
            content=message.get("content") or "",
            tool_calls=self._decode_tool_calls(message.get("tool_calls") or []),
            model=body.get("model") or self._chat_model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason") or "",
        )

    # ---------------------------------------------------------------- vision

    async def read_image(
        self,
        image: bytes,
        mime_type: str,
        *,
        instruction: str,
        schema: dict[str, Any],
        timeout_seconds: float,
    ) -> VisionReading:
        """Read an image into the object `schema` describes (`VisionProvider`).

        The image goes inline as a base64 data URL, so nothing has to be reachable
        from the provider's side, and the answer is constrained by a JSON schema so
        an amount is a field, not a sentence to parse.
        """
        encoded = base64.b64encode(image).decode("ascii")
        payload: dict[str, Any] = {
            "model": self._vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            self._max_tokens_field: 400,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "reading", "strict": True, "schema": schema},
            },
        }
        started = time.perf_counter()
        body = await self._post(
            "/chat/completions",
            payload,
            limit=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0)),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        choices = body.get("choices") or []
        if not choices:
            raise LLMError("provider returned no choices")
        content = (choices[0].get("message") or {}).get("content") or ""
        try:
            data = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise LLMError("the vision answer was not JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("the vision answer was not a JSON object")
        usage = body.get("usage") or {}
        return VisionReading(
            data=data,
            model=body.get("model") or self._vision_model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------- embeddings

    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "passage"
    ) -> list[list[float]]:
        if not texts:
            return []

        payload: dict[str, Any] = {
            "model": self._embedding_model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if self._send_embedding_input_type:
            payload["input_type"] = input_type
        if self._embedding_dimensions is not None:
            payload["dimensions"] = self._embedding_dimensions

        body = await self._post("/embeddings", payload)
        data = body.get("data") or []
        if len(data) != len(texts):
            raise LLMError(f"expected {len(texts)} embeddings, provider returned {len(data)}")
        # Providers are not required to return items in request order.
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [list(item["embedding"]) for item in ordered]
