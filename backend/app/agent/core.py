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
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agent.catalog_guard import CatalogueGuard, build_catalogue_guard
from app.agent.prompts.builder import build_name_correction, build_system_prompt
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
from app.repositories.product import ProductRepository

logger = logging.getLogger(__name__)

# How many times the model may call tools before it has to answer. Two is
# enough for the real pattern - look up the product, then look up the delivery
# policy - and bounds a loop that would otherwise bill per iteration.
MAX_TOOL_ROUNDS = 2

# Conversation *messages* - not exchanges - replayed into the prompt. At ten
# the agent lost track inside a single order: a customer who said "para recoger
# en Bagua" was asked for a delivery address four turns later, because the
# sentence had already fallen out of the window. Thirty covers the ordering
# conversations this is actually used for, at roughly 1.8k tokens of history,
# and still leaves the tool results room to be the part that decides the
# answer. A rolling summary (TASK-304) is what removes the ceiling; this only
# raises it.
HISTORY_TURNS = 30

FALLBACK_REPLY = (
    "Disculpa, no pude procesar tu mensaje en este momento. "
    "¿Podrías intentarlo de nuevo en unos segundos?"
)

# Sent when the model named a product the catalogue does not have, was told
# so, and named one again. Saying nothing useful is the right answer here:
# the alternative is sending a product name the business cannot honour, and
# the customer finds that out standing at the counter.
CATALOG_GUARD_REPLY = (
    "Disculpa, no quiero darte el nombre equivocado de un producto. "
    "Déjame comunicarte con una persona del negocio para confirmártelo bien."
)

# The most product names the guard will hold to judge a reply against. A
# catalogue larger than this makes the guard *stand down* rather than judge on
# a partial list: a real product missing from the sample would look invented,
# and a guard that apologises for a correct answer is worse than no guard.
# Far above any catalogue a WhatsApp business actually keeps, and still
# bounded so one turn cannot pull an unbounded result set.
MAX_GUARDED_CATALOG_NAMES = 1000


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

# Below this, what *survived a strip* is a fragment of the call rather than a
# reply. It only applies when markup was actually removed: a reply that never
# contained any is a reply however short it is. "¡De nada! 👍" is the right
# answer to a customer who said "ok", and replacing it with an apology for a
# failure that did not happen is the worse error of the two.
_MIN_ANSWER_CHARS = 12

# WhatsApp's markup is not Markdown, and every model defaults to Markdown.
# `**negrita**` arrives with the asterisks showing, which reads to a customer
# as a bot that is broken. The prompt asks for the right syntax; this converts
# it regardless, because formatting is exactly the instruction a model drifts
# away from once the conversation gets long.
#
# `(?=\S)` and `(?<=\S)` keep the delimiters glued to real text, so arithmetic
# like `2 ** 3` and a row of `***` separators are left alone.
_MD_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_MD_BOLD_UNDERSCORE = re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.DOTALL)
_MD_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
# Headings have no equivalent at all: the marker is dropped and the text kept.
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)


def whatsapp_text(text: str) -> str:
    """Rewrite Markdown emphasis as the markup WhatsApp actually renders.

    Single-underscore `_cursiva_` means the same thing in both, so it is left
    untouched.
    """
    text = _MD_BOLD.sub(r"*\1*", text)
    text = _MD_BOLD_UNDERSCORE.sub(r"*\1*", text)
    text = _MD_STRIKE.sub(r"~\1~", text)
    return _MD_HEADING.sub("", text)


_RUN_OF_BLANKS = re.compile(r"[ \t]+")
_TRAILING_BLANKS = re.compile(r"[ \t]+$", re.MULTILINE)
_RUN_OF_NEWLINES = re.compile(r"\n{3,}")


def _tidy(text: str) -> str:
    """Normalise spacing without flattening the reply onto one line.

    This used to be `" ".join(text.split())`, which also ate every newline -
    so a price list the model had laid out one product per line arrived as a
    single run-on sentence with dashes in it, which is precisely the shape
    nobody can read on a phone. Runs of blanks collapse, blank-line runs cap
    at one, and real line breaks survive.
    """
    text = _RUN_OF_BLANKS.sub(" ", text)
    text = _TRAILING_BLANKS.sub("", text)
    return _RUN_OF_NEWLINES.sub("\n\n", text).strip()


def _usable_answer(raw: str) -> tuple[str, bool]:
    """The reply to show, and whether the model failed to produce one.

    Any leftover tool-call markup is removed rather than shown. The answer is
    treated as absent - the customer gets the fallback and the trace records
    `degraded`, so the tenant can see it was the model that failed and not
    their configuration - when nothing is left after the strip, or when the
    strip removed markup and left too little behind to be anything but a
    fragment of the call. A short answer that carried no markup is kept.

    Whatever survives is rewritten into WhatsApp's markup on the way out.
    """
    text = raw.strip()
    if not text:
        return FALLBACK_REPLY, True

    stripped = _TOOL_BLOCK.sub(" ", text)
    cleaned = _tidy(stripped)

    if not cleaned:
        return FALLBACK_REPLY, True
    if stripped != text and len(cleaned) < _MIN_ANSWER_CHARS:
        return FALLBACK_REPLY, True
    return whatsapp_text(cleaned), False


# One turn's tool results, keyed by tool name and arguments.
_ToolCache = dict[tuple[str, str], tuple[str, list[dict[str, object]]]]


@dataclass(frozen=True)
class _GuardOutcome:
    """What the catalogue post-guard decided about one reply.

    `blocked` is carried rather than inferred from the content, so a model
    that happens to write the guarded fallback word for word is not recorded
    as having been stopped by a guard that never fired.
    """

    content: str
    note: dict[str, object] | None = None
    blocked: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


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
    # What the catalogue post-guard did, or None when it had nothing to say -
    # which is every ordinary turn. See `_enforce_catalogue_names`.
    catalog_guard: dict[str, object] | None = None


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
        """The instructions this tenant's agent runs under.

        The clock is read here, at the edge, so the builder itself stays pure
        and the evals can pin a date.
        """
        return build_system_prompt(
            profile=BusinessProfileRepository(self._session, self._tenant_id).current(),
            config=AgentConfigRepository(self._session, self._tenant_id).current(),
            tool_names=self._registry.names(),
            now=datetime.now(UTC),
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
        # Models repeat themselves: `consultar_catalogo` came back seven times
        # for the same term in one conversation. Within a turn the answer
        # cannot have changed, so it is looked up once. The cache dies with
        # this call - a catalogue edited between two customer messages must
        # still be seen on the next one.
        tool_cache: _ToolCache = {}
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
                output, sources, cached = await self._run_tool(context, call, tool_cache)
                # A served-from-cache call is still recorded: a model asking
                # the same thing seven times is a prompt problem, and a trace
                # that hides the repeats is a trace that hides it.
                tools_called.append(
                    {
                        "tool": call.name,
                        "args_resumen": _summarise(call.arguments),
                        "ok": not output.startswith("ERROR:"),
                        "cached": cached,
                    }
                )
                rag_sources.extend(sources)
                messages.append(ChatMessage("tool", output, tool_call_id=call.id, name=call.name))

        # A model that answers nothing, or answers with tool-call syntax, is
        # worse than one that says it cannot help: the customer sees an empty
        # bubble or a pile of angle brackets, and no next step either way.
        content, unusable = _usable_answer(result.content if result else "")

        guard_note: dict[str, object] | None = None
        if not unusable:
            guarded = await self._enforce_catalogue_names(messages, content)
            content = guarded.content
            guard_note = guarded.note
            input_tokens += guarded.input_tokens
            output_tokens += guarded.output_tokens
            unusable = guarded.blocked

        return AgentReply(
            content=content,
            model=result.model if result else self._llm.chat_model,
            tools_called=tools_called,
            rag_sources=rag_sources,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            degraded=result is None or unusable,
            catalog_guard=guard_note,
        )

    async def _enforce_catalogue_names(
        self, messages: list[ChatMessage], content: str
    ) -> _GuardOutcome:
        """Refuse to send a product name the tenant's catalogue does not have.

        The catalogue said "Leche" and the agent offered "Leche pausterizada".
        Rule 1 of the prompt already forbade that and did not stop it, because
        a name that is *almost* right does not feel invented to a model. So the
        finished reply is checked against the real catalogue before it leaves.

        On a hit the model gets **one** retry, handed the exact names it is
        allowed to use. One and not more: a second offence after being shown
        the list is a model that is not going to comply, and every further
        attempt is another few seconds of a customer watching "typing...".
        If the retry offends too, the invented name is not sent at all.

        The catalogue is read **once per turn**, lazily: a turn that never
        produces a usable answer never pays for it, and the retry is judged by
        the same `CatalogueGuard` instance rather than a second query.
        """
        guard = self._catalogue_guard()
        invented = guard.invented_names(content)
        if not invented:
            return _GuardOutcome(content=content)

        logger.warning(
            "agent named a product outside the catalogue",
            extra={"tenant_id": str(self._tenant_id), "invented": invented},
        )

        messages.append(ChatMessage("assistant", content))
        messages.append(ChatMessage("user", build_name_correction(invented, guard.catalogue)))
        try:
            retry = await self._llm.chat(messages, tools=())
        except LLMError:
            # The retry was the only thing that could have rescued this
            # answer. Without it the original still names a product that does
            # not exist, so it still may not be sent.
            logger.exception(
                "catalogue correction failed", extra={"tenant_id": str(self._tenant_id)}
            )
            return _GuardOutcome(
                content=CATALOG_GUARD_REPLY,
                note=_guard_note("blocked", invented, []),
                blocked=True,
            )

        corrected, unusable = _usable_answer(retry.content)
        still_invented = guard.invented_names(corrected)
        blocked = unusable or bool(still_invented)

        return _GuardOutcome(
            content=CATALOG_GUARD_REPLY if blocked else corrected,
            note=_guard_note("blocked" if blocked else "corrected", invented, still_invented),
            blocked=blocked,
            # The correction is a real model call on a real bill. A trace that
            # hides it under-reports the cost of a prompt that keeps slipping.
            input_tokens=retry.input_tokens,
            output_tokens=retry.output_tokens,
        )

    def _catalogue_guard(self) -> CatalogueGuard:
        """This tenant's catalogue names, in a form that can judge a reply.

        Called exactly once per turn - `_enforce_catalogue_names` holds the
        result and checks both the first answer and the retried one against
        it - so a turn costs one short indexed read however the guard goes,
        and none at all when the model never produced a usable answer. The
        result is deliberately *not* kept on the instance: a catalogue edited
        between two customer messages must be seen on the next one, the same
        rule the per-turn tool cache follows.

        Names only, and never another tenant's: `ProductRepository` is bound
        to one `tenant_id` and filters on it, so there is no way to reach a
        catalogue this agent is not answering for.

        The catalogue *tool*'s output is not reused for this even when the
        turn already ran it. That listing is capped and filtered by whatever
        the model searched for, so a real product missing from it would look
        invented - the guard would then apologise for a correct answer, which
        is the failure this whole file is arranged to avoid.
        """
        names = ProductRepository(self._session, self._tenant_id).active_names(
            limit=MAX_GUARDED_CATALOG_NAMES + 1
        )
        if len(names) > MAX_GUARDED_CATALOG_NAMES:
            logger.warning(
                "catalogue too large to guard; standing down",
                extra={"tenant_id": str(self._tenant_id), "names": len(names)},
            )
            names = []
        # No vocabulary: at runtime the agent knows its catalogue and nothing
        # else, so the introduced-name half of the detector would fire on
        # "Miraflores", "Yape" and the customer's own name.
        return build_catalogue_guard(names)

    async def _run_tool(
        self, context: ToolContext, call: ToolCall, cache: _ToolCache
    ) -> tuple[str, list[dict[str, object]], bool]:
        """Execute one tool call, converting any failure into text.

        A raised exception here would abort the whole turn over one bad tool
        argument. Handing the model the error instead lets it apologise, or
        retry with something sensible, which is what a person would do.

        The third value says whether the result came from `cache` rather than
        from the tool. Failures are never cached: an error is the one result
        that is worth attempting again.
        """
        key = (call.name, _cache_key(call.arguments))
        remembered = cache.get(key)
        if remembered is not None:
            return remembered[0], remembered[1], True

        tool = self._registry.get(call.name)
        if tool is None:
            # Models do hallucinate tool names. Saying so is more useful than
            # silence, and it is a real signal in the trace.
            return f"ERROR: la herramienta '{call.name}' no existe.", [], False

        try:
            output = await tool.handler(context, call.arguments)
        except Exception:
            logger.exception(
                "tool failed", extra={"tool": call.name, "tenant_id": str(self._tenant_id)}
            )
            return f"ERROR: no se pudo consultar '{call.name}' en este momento.", [], False

        sources: list[dict[str, object]] = []
        if call.name == "consultar_conocimiento" and not output.startswith("No hay información"):
            sources.append({"tool": call.name, "consulta": _summarise(call.arguments)})

        cache[key] = (output, sources)
        return output, sources, False

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
            catalog_guard=reply.catalog_guard,
        )


def _guard_note(status: str, invented: list[str], still_invented: list[str]) -> dict[str, object]:
    """What the catalogue guard did, for the trace.

    Written only when the guard actually fired - an ordinary turn leaves this
    null - so its presence on a trace is itself the signal, and there is
    always exactly one retry behind it.

    `invented` is what the first answer claimed; `still_invented` is what
    survived the correction, and is empty when the retry came back clean or
    never came back at all. The two are separated because "the model fixed it
    when told" and "the model said it again" are different problems for
    whoever reads this: the first is a prompt to tighten, the second is a
    model to change.
    """
    return {
        "status": status,
        "invented": invented,
        "still_invented": still_invented,
    }


def _cache_key(arguments: dict[str, object]) -> str:
    """A stable identity for one set of tool arguments.

    Sorted, so the same question asked with the keys in a different order is
    recognised as the same question.
    """
    try:
        return json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(arguments)


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
