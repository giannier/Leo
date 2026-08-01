"""Agent-core v0: one customer message in, one answer plus a trace out.

Scope is Clarification 010 deliberately: a real agent, not a mock, but only
the LLM adapter, a prompt built from the tenant's configuration, two tools and
trace persistence. No media, no vision, no memory across conversations. Those
arrive in Features 003 and 004, and this is built on the interfaces they will
extend so they only add.

**Every reply is traced.** `agent_traces` records the model, the tools it
called, the sources it retrieved and the tokens it spent. That is not
analytics: when a customer says "your bot told me the wrong price", the trace
is the only way to find out whether the catalogue was wrong, the retrieval
missed, or the model invented it. Without it the three are indistinguishable
and none of them can be fixed.

**The loop is bounded.** A model that keeps calling tools forever costs money
per turn and leaves the customer waiting. When the budget runs out the agent
answers with what it has rather than failing, because a partial answer beats
silence in a chat.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agent.prompts.builder import build_system_prompt
from app.agent.tools.catalog import build_default_registry
from app.agent.tools.registry import ToolContext, ToolRegistry
from app.models.conversation import Message
from app.providers.llm.base import (
    ChatMessage,
    ChatResult,
    LLMError,
    LLMProvider,
    ToolCall,
)
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.conversation import AgentTraceRepository, MessageRepository

logger = logging.getLogger(__name__)

# How many times the model may call tools before it has to answer. Two is
# enough for the real pattern - look up the product, then look up the delivery
# policy - and bounds a loop that would otherwise bill per iteration.
MAX_TOOL_ROUNDS = 2

# Conversation turns replayed into the prompt. Enough to follow a thread
# without letting a long chat crowd out the tool results, which are the part
# that keeps the answer truthful.
HISTORY_TURNS = 10

FALLBACK_REPLY = (
    "Disculpa, no pude procesar tu mensaje en este momento. "
    "¿Podrías intentarlo de nuevo en unos segundos?"
)


# Tool-call syntax that a model sometimes writes as *prose* instead of
# emitting through the structured channel.
#
# It happens in the final round, where no tools are offered in order to force
# an answer: a model that still wants to call one has nowhere to put the call,
# so it writes it out. Nemotron produces `<function=name><parameter=x>`,
# others `<tool_call>` or `<|python_tag|>`.
#
# Left alone this reaches the customer verbatim. A person writing an angry
# complaint received:
#
#     <function=consultar_conocimiento>
#     <parameter=consulta>reclamo queja</parameter>
#     </function>
#
# which is worse than saying nothing: it looks broken *and* it ignores them.
_TOOL_BLOCK = re.compile(
    r"<\s*(?:function|tool_call|tool_response)\b.*?"
    r"(?:</\s*(?:function|tool_call|tool_response)\s*>|$)"
    r"|<\s*/?\s*(?:function|parameter|tool_call|tool_response)\b[^>]*>"
    r"|<\|[^|]{1,40}\|>",
    re.IGNORECASE | re.DOTALL,
)

# Below this, whatever survived the strip is a fragment rather than a reply.
_MIN_ANSWER_CHARS = 12


def _usable_answer(raw: str) -> tuple[str, bool]:
    """The reply to show, and whether the model failed to produce one.

    Any leftover tool-call markup is removed rather than shown. If that
    leaves nothing substantive the answer is treated as absent - the customer
    gets the fallback, and the trace records `degraded` so the tenant can see
    it was the model that failed and not their configuration.
    """
    text = raw.strip()
    if not text:
        return FALLBACK_REPLY, True

    cleaned = _TOOL_BLOCK.sub(" ", text)
    cleaned = " ".join(cleaned.split()).strip()

    if len(cleaned) < _MIN_ANSWER_CHARS:
        return FALLBACK_REPLY, True
    # Prose survived alongside the markup: keep the prose.
    return cleaned if cleaned != text else text, False


@dataclass
class AgentReply:
    """What the agent produced, and everything needed to explain it."""

    content: str
    model: str
    tools_called: list[dict[str, object]] = field(default_factory=list)
    rag_sources: list[dict[str, object]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    # True when the answer is the canned fallback rather than the model's.
    degraded: bool = False


class AgentCore:
    """Answers one message for one tenant."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        llm: LLMProvider,
        registry: ToolRegistry | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        self._registry = registry or build_default_registry()
        self._max_rounds = max_tool_rounds

    def system_prompt(self) -> str:
        """The instructions this tenant's agent runs under."""
        return build_system_prompt(
            profile=BusinessProfileRepository(self._session, self._tenant_id).current(),
            config=AgentConfigRepository(self._session, self._tenant_id).current(),
            tool_names=self._registry.names(),
        )

    async def respond(self, *, conversation_id: uuid.UUID, customer_message: str) -> AgentReply:
        """Produce one reply, running tools as the model asks for them."""
        started = time.perf_counter()
        messages: list[ChatMessage] = [
            ChatMessage("system", self.system_prompt()),
            *self._history(conversation_id),
            ChatMessage("user", customer_message),
        ]

        context = ToolContext(session=self._session, tenant_id=self._tenant_id, llm=self._llm)
        tools_called: list[dict[str, object]] = []
        rag_sources: list[dict[str, object]] = []
        input_tokens = 0
        output_tokens = 0
        result: ChatResult | None = None

        for round_index in range(self._max_rounds + 1):
            # The last round offers no tools, which is what forces an answer.
            # Leaving them available would let the model call tools until the
            # budget ran out and then return nothing.
            offer_tools = round_index < self._max_rounds
            try:
                result = await self._llm.chat(
                    messages,
                    tools=self._registry.specs() if offer_tools else (),
                )
            except LLMError:
                logger.exception("agent turn failed", extra={"tenant_id": str(self._tenant_id)})
                return AgentReply(
                    content=FALLBACK_REPLY,
                    model=self._llm.chat_model,
                    tools_called=tools_called,
                    rag_sources=rag_sources,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    degraded=True,
                )

            input_tokens += result.input_tokens
            output_tokens += result.output_tokens

            if not result.wants_tool:
                break

            # The full turn, tool calls included. Replaying only the text
            # leaves the `tool` results below answering nothing, which OpenAI
            # rejects outright.
            messages.append(ChatMessage("assistant", result.content, tool_calls=result.tool_calls))
            for call in result.tool_calls:
                output, sources = await self._run_tool(context, call)
                tools_called.append(
                    {
                        "tool": call.name,
                        "args_resumen": _summarise(call.arguments),
                        "ok": not output.startswith("ERROR:"),
                    }
                )
                rag_sources.extend(sources)
                messages.append(ChatMessage("tool", output, tool_call_id=call.id, name=call.name))

        # A model that answers nothing, or answers with tool-call syntax, is
        # worse than one that says it cannot help: the customer sees an empty
        # bubble or a pile of angle brackets, and no next step either way.
        content, unusable = _usable_answer(result.content if result else "")

        return AgentReply(
            content=content,
            model=result.model if result else self._llm.chat_model,
            tools_called=tools_called,
            rag_sources=rag_sources,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            degraded=result is None or unusable,
        )

    async def _run_tool(
        self, context: ToolContext, call: ToolCall
    ) -> tuple[str, list[dict[str, object]]]:
        """Execute one tool call, converting any failure into text.

        A raised exception here would abort the whole turn over one bad tool
        argument. Handing the model the error instead lets it apologise, or
        retry with something sensible, which is what a person would do.
        """
        tool = self._registry.get(call.name)
        if tool is None:
            # Models do hallucinate tool names. Saying so is more useful than
            # silence, and it is a real signal in the trace.
            return f"ERROR: la herramienta '{call.name}' no existe.", []

        try:
            output = await tool.handler(context, call.arguments)
        except Exception:
            logger.exception(
                "tool failed", extra={"tool": call.name, "tenant_id": str(self._tenant_id)}
            )
            return f"ERROR: no se pudo consultar '{call.name}' en este momento.", []

        sources: list[dict[str, object]] = []
        if call.name == "consultar_conocimiento" and not output.startswith("No hay información"):
            sources.append({"tool": call.name, "consulta": _summarise(call.arguments)})
        return output, sources

    def _history(self, conversation_id: uuid.UUID) -> list[ChatMessage]:
        """Recent turns of this conversation, oldest first."""
        rows = MessageRepository(self._session, self._tenant_id).list_for_conversation(
            conversation_id
        )
        recent = list(rows)[-HISTORY_TURNS:]
        return [
            ChatMessage("user" if message.author == "customer" else "assistant", message.content)
            for message in recent
        ]

    def persist_trace(self, *, message: Message, reply: AgentReply) -> None:
        """Record how this answer was produced.

        Written for every reply, including the degraded one - a turn that
        failed is exactly the turn somebody will ask about later.
        """
        AgentTraceRepository(self._session, self._tenant_id).add(
            message_id=message.id,
            model=reply.model,
            tools_called=reply.tools_called,
            rag_sources=reply.rag_sources,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            latency_ms=reply.latency_ms,
        )


def _summarise(arguments: dict[str, object], limit: int = 120) -> str:
    """A short, readable record of what a tool was asked.

    Summarised rather than stored whole: arguments can carry a customer's
    entire message, and a trace is not the place to accumulate a second copy
    of the conversation.
    """
    try:
        rendered = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        rendered = str(arguments)
    return rendered[:limit]
