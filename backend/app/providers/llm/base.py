"""The `LLMProvider` port.

The agent-core talks to this interface and nothing else. Swapping NVIDIA for
OpenAI, or either for a mock, must never require a change above this layer -
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
