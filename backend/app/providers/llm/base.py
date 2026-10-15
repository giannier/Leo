"""The `LLMProvider` port.

The agent-core talks to this interface and nothing else. Swapping OpenAI for
another vendor, or for a mock, must never require a change above this layer -
that is the whole point of Ports and Adapters (Constitution).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]
InputType = Literal["query", "passage"]


class LLMConfigurationError(RuntimeError):
    """The selected profile is not usable: no credential, or an implausible one.

    Lives here rather than in `factory` so a provider can raise it without
    importing the factory that builds providers.
    """


class LLMError(RuntimeError):
    """Any failure reaching or parsing a provider response."""


class LLMAuthError(LLMError):
    """The credential was rejected.

    Separate from `LLMError` because the remedy is different: the customer
    must fix their key, and retrying will never help.
    """


class LLMRateLimitError(LLMError):
    """The provider is throttling. Worth retrying, unlike the others."""


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    # Set on `tool` messages to link a result back to its call.
    tool_call_id: str | None = None
    name: str | None = None
    # Set on the `assistant` turn that *requested* tools, and it has to be:
    # the OpenAI API rejects a `tool` message whose preceding assistant turn
    # does not carry the matching `tool_calls`. NVIDIA's endpoint tolerated
    # the omission, so the agent loop replayed only the assistant's text and
    # worked - right up until the profile was switched to OpenAI, where every
    # tool-using turn then failed and fell back.
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    """A tool the model wants executed, as returned by the provider."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    """One model turn.

    `content` and `tool_calls` are not mutually exclusive: some models emit a
    sentence alongside a call. The agent-core must handle both being present.
    """

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model, in provider-neutral form."""

    name: str
    description: str
    # JSON Schema for the arguments object.
    parameters: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Chat completion with tool calling, plus embeddings."""

    @property
    def chat_model(self) -> str: ...

    @property
    def embedding_model(self) -> str: ...

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> ChatResult:
        """Produce one assistant turn."""
        ...

    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "passage"
    ) -> list[list[float]]:
        """Embed texts for retrieval.

        `input_type` matters: retrieval quality drops measurably when the
        query and the stored passages are embedded the same way, because
        these models are trained asymmetrically. Providers that do not
        support the distinction ignore it.
        """
        ...


@dataclass(frozen=True)
class VisionReading:
    """What a model made of one image, as the JSON object the caller's schema asked for."""

    data: dict[str, Any]
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


@runtime_checkable
class VisionProvider(Protocol):
    """A provider that can read an image into structured data (Feature 004, C-08).

    A separate port from `LLMProvider` on purpose: the chat port is protected, and
    not every model behind it can see. The caller asks `isinstance(llm,
    VisionProvider)` and, when the answer is no, records the image without reading
    it. The image travels as bytes; how a vendor wants it encoded (a data URL, a
    file id) stops inside the adapter.
    """

    async def read_image(
        self,
        image: bytes,
        mime_type: str,
        *,
        instruction: str,
        schema: dict[str, Any],
        timeout_seconds: float,
    ) -> VisionReading:
        """Read `image` into an object that follows `schema`, or raise `LLMError`."""
        ...


@runtime_checkable
class DocumentReader(Protocol):
    """A provider that can read a PDF page with a vision model (Feature 006, TASK-602).

    Used for scanned documents: each page goes on its own, so a page the model
    cannot read is reported by number and the others are still indexed. Separate
    from `VisionProvider` because a vendor can read images and not documents.
    """

    async def read_document(
        self,
        document: bytes,
        *,
        instruction: str,
        schema: dict[str, Any],
        timeout_seconds: float,
        max_tokens: int,
    ) -> VisionReading:
        """Read a PDF into an object that follows `schema`, or raise `LLMError`."""
        ...
