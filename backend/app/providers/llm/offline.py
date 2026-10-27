"""A deterministic `LLMProvider` that talks to nobody. Development only.

Exists so the product can be walked end to end with no credential and no
network: the sandbox, knowledge indexing and RAG retrieval all work, and none
of it costs anything or leaves the machine.

Without it neither a developer nor CI can exercise the sandbox at all, because
every path through it ends at an embedding or a completion call. That is a
poor reason to leave a screen untested.

**It is not a model, and it never pretends to be.** Every reply says so in the
first line. The failure this guards against is somebody demoing the offline
mode, seeing sensible-looking Spanish, and concluding the agent works.

It refuses to construct outside development, exactly like
`ConsoleEmailProvider` and `ConsoleOtpSender`.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Sequence
from typing import Any, Literal

from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.providers.llm.base import (
    ChatMessage,
    ChatResult,
    LLMConfigurationError,
    LLMError,
    ToolSpec,
    VisionReading,
)

CHAT_MODEL = "offline-deterministic"
EMBEDDING_MODEL = "offline-hashing"

# The banner every reply opens with. Deliberately impossible to mistake for
# the product, and deliberately in the same language as the rest of the panel
# so it reads as a state rather than as a crash.
BANNER = "[MODO SIN CONEXION - esto no es tu agente, es una respuesta de prueba]"

_TOKEN = re.compile(r"[a-z0-9á-ú]+")


class OfflineLLMProvider:
    """Answers from a hash. Deterministic, offline, and honest about it."""

    def __init__(self, *, environment: str, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        if environment not in ("development", "test"):
            raise LLMConfigurationError(
                "OfflineLLMProvider answers from a hash and must never run in "
                f"{environment!r}. Configure a real LLM_PROFILE instead."
            )
        self._dimensions = dimensions

    @property
    def chat_model(self) -> str:
        return CHAT_MODEL

    @property
    def embedding_model(self) -> str:
        return EMBEDDING_MODEL

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.2,
        max_tokens: int = 1024,
        cache_key: str | None = None,
    ) -> ChatResult:
        """Echo the question back inside an unmistakable banner.

        No tool calls are ever requested. Choosing a tool is a judgement about
        meaning, and a hash cannot make one - inventing a plausible-looking
        call would put fiction in the trace panel, which is the one place that
        has to be trustworthy.
        """
        started = time.perf_counter()
        question = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        shortened = question.strip()[:160]

        content = (
            f"{BANNER}\n\n"
            f'Recibi tu mensaje: "{shortened}".\n\n'
            "Para ver respuestas reales configura LLM_PROFILE con un proveedor "
            "y su clave, y reinicia el backend."
        )
        return ChatResult(
            content=content,
            tool_calls=(),
            model=CHAT_MODEL,
            # Rough, and rough on purpose: a made-up exact count in the trace
            # would be read as real usage.
            input_tokens=sum(len(message.content) for message in messages) // 4,
            output_tokens=len(content) // 4,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def read_image(
        self,
        image: bytes,
        mime_type: str,
        *,
        instruction: str,
        schema: dict[str, Any],
        timeout_seconds: float,
    ) -> VisionReading:
        """Refuses: a hash cannot read a receipt, and a made-up amount is worse than none.

        The caller treats this like any provider that cannot see: the proof is
        recorded without data and the operator reads the image.
        """
        raise LLMError("the offline provider cannot read images")

    async def read_document(
        self,
        document: bytes,
        *,
        instruction: str,
        schema: dict[str, Any],
        timeout_seconds: float,
        max_tokens: int,
    ) -> VisionReading:
        """Refuses, like `read_image`: a scanned page is reported as not read."""
        raise LLMError("the offline provider cannot read documents")

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: Literal["query", "passage"] = "passage",
    ) -> list[list[float]]:
        """The hashing trick: each token lands in a bucket, buckets are counted.

        Not semantic - it cannot be, offline - but not random either. Two
        texts sharing words score closer than two that share none, so RAG
        retrieval returns something defensible instead of an arbitrary
        ordering. That distinction matters: random results would let somebody
        conclude retrieval is broken, or that it works, on no evidence.

        `input_type` is accepted and ignored. The port carries it because
        some compatible endpoints require it and OpenAI rejects it; here there is no provider to
        tell, and a query and a passage of the same words should land in the
        same place anyway.
        """
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        buckets = [0.0] * self._dimensions
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self._dimensions
            # Signed, so unrelated tokens can cancel instead of only ever
            # pushing every vector into the same corner of the space.
            buckets[index] += 1.0 if digest[0] & 1 else -1.0

        norm = math.sqrt(sum(value * value for value in buckets))
        if norm == 0.0:
            # An empty or punctuation-only chunk. A zero vector is undefined
            # under cosine distance, so it gets one fixed unit direction -
            # every such chunk is then equally, and equally uselessly, far
            # from everything.
            buckets[0] = 1.0
            return buckets
        return [value / norm for value in buckets]
