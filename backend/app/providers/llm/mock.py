"""In-memory `LLMProvider` for tests.

Scripted rather than random: a test asserting agent behaviour must control
exactly what the model returns. Also records every call, so tests can assert
on the prompt that was actually built.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.providers.llm.base import (
    ChatMessage,
    ChatResult,
    InputType,
    LLMError,
    ToolSpec,
)


@dataclass
class RecordedChat:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...]
    temperature: float
    max_tokens: int


@dataclass
class RecordedEmbed:
    texts: tuple[str, ...]
    input_type: InputType


class MockLLMProvider:
    """Returns queued results and records what it was asked."""

    def __init__(
        self,
        *,
        responses: Sequence[ChatResult] = (),
        chat_model: str = "mock-chat",
        embedding_model: str = "mock-embed",
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self._responses = list(responses)
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._dimensions = dimensions
        self.chat_calls: list[RecordedChat] = []
        self.embed_calls: list[RecordedEmbed] = []

    @property
    def chat_model(self) -> str:
        return self._chat_model

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    def queue(self, result: ChatResult) -> None:
        self._responses.append(result)

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> ChatResult:
        self.chat_calls.append(RecordedChat(tuple(messages), tuple(tools), temperature, max_tokens))
        if not self._responses:
            raise LLMError(
                "MockLLMProvider ran out of queued responses. Queue one per "
                "expected model turn - an agent loop calls chat() repeatedly."
            )
        return self._responses.pop(0)

    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "passage"
    ) -> list[list[float]]:
        self.embed_calls.append(RecordedEmbed(tuple(texts), input_type))
        return [self._deterministic_vector(text) for text in texts]

    def _deterministic_vector(self, text: str) -> list[float]:
        """Stable pseudo-embedding derived from the text.

        Deterministic so the same text always embeds identically - retrieval
        tests need that - while different texts stay far apart.
        """
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = struct.unpack("<Q", digest[:8])[0]
        values: list[float] = []
        state = seed or 1
        for _ in range(self._dimensions):
            state = (state * 6364136223846793005 + 1442695040888963407) % (2**64)
            values.append(((state >> 11) / float(2**53)) * 2.0 - 1.0)
        return values
