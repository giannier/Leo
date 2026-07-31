"""Live contract check against the configured LLM provider.

Marked `slow` and therefore excluded from the default run: it needs network
and a real credential. Run it deliberately with `pytest -m slow`.

Its job is the one thing mocks structurally cannot verify - that the provider
still behaves the way the adapter assumes. Tool-calling support and embedding
width are both things a vendor can change without warning, and both would
break the agent silently.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.models.knowledge import EMBEDDING_DIMENSIONS
from app.providers.llm import (
    ChatMessage,
    LLMConfigurationError,
    ToolSpec,
    platform_profile,
    platform_provider,
)

pytestmark = pytest.mark.slow


@pytest.fixture
def live_provider():  # type: ignore[no-untyped-def]
    try:
        profile = platform_profile(get_settings())
    except LLMConfigurationError as exc:
        pytest.skip(str(exc))
    return platform_provider(), profile


class TestLiveProvider:
    async def test_the_model_still_calls_tools(self, live_provider) -> None:  # type: ignore[no-untyped-def]
        """A vendor silently dropping tool support would break the agent."""
        provider, _ = live_provider
        result = await provider.chat(
            [
                ChatMessage("system", "Sos el asistente de un restaurante. Usa las herramientas."),
                ChatMessage("user", "Cuanto cuesta el lomo saltado?"),
            ],
            tools=[
                ToolSpec(
                    name="consultar_catalogo",
                    description="Busca productos del negocio por nombre o categoria.",
                    parameters={
                        "type": "object",
                        "properties": {"consulta": {"type": "string"}},
                        "required": ["consulta"],
                    },
                )
            ],
        )
        assert result.wants_tool, f"model answered without calling a tool: {result.content!r}"
        assert result.tool_calls[0].name == "consultar_catalogo"
        assert "consulta" in result.tool_calls[0].arguments

    async def test_usage_and_latency_are_reported(self, live_provider) -> None:  # type: ignore[no-untyped-def]
        """Traces record token counts from day one (plan.md)."""
        provider, _ = live_provider
        result = await provider.chat([ChatMessage("user", "Responde solo: ok")])
        assert result.input_tokens > 0
        assert result.latency_ms > 0

    async def test_embedding_width_matches_the_database_column(self, live_provider) -> None:  # type: ignore[no-untyped-def]
        """The single most damaging drift: vectors that no longer fit."""
        provider, _ = live_provider
        vectors = await provider.embed(["horario de atencion"], input_type="query")
        assert len(vectors[0]) == EMBEDDING_DIMENSIONS
