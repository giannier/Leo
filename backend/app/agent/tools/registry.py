"""The tool registry the agent-core loops over.

A registry rather than a hardcoded `if name == ...` chain, because Features 003
and 004 add tools by the handful - media, vision, receipt validation - and the
loop must not need editing to accept them. Registering is the whole integration:
declare the schema, write the handler, and the model can call it.

Every handler receives a `ToolContext` carrying the tenant. That is not
convenience: it is what makes a tool unable to read outside its own business even
if its implementation forgets, because the repositories it is handed are already
bound.

Feature 004 adds three things on top of that:

* **`ToolResult`**, the one envelope every tool answers with
  (`contracts/api.md` section 7): a status, data, and a short instruction on what
  to tell the customer. Handlers may still return plain text (the v0 catalogue
  tool does), and the loop accepts both.
* **`ToolCatalog.for_stage`**, which builds the registry of one turn: the tools of
  the stage the conversation is in plus the global ones, with the schema and the
  guards adapted to the tenant (`plantillas.md` section 2).
* A richer **`ToolContext`**: the turn budget, the outbound adapter, the sandbox
  flag, what is true only this turn, the facts the stage was computed from and the
  patch the tools add to them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.agent.budget import TurnBudget
from app.agent.outbound import TurnOutbound
from app.orders.notices import OrderNotices
from app.process.facts import Facts
from app.process.templates.base import (
    GLOBAL_TOOLS,
    LEAD_TOOL_NAMES,
    POSTSALE_TOOL_NAMES,
    QUOTE_TOOL_NAMES,
    SCHEDULING_TOOL_NAMES,
    EffectiveOptions,
    Stage,
    Template,
)
from app.process.turn import TurnContext
from app.providers.llm.base import LLMProvider, ToolSpec

ToolStatus = Literal["hecho", "omitido", "no_disponible", "error"]


@dataclass(frozen=True)
class ToolResult:
    """What a tool tells the model (`contracts/api.md` section 7).

    * `hecho`: it did what was asked.
    * `omitido`: it had already been done earlier (do not repeat or mention it).
    * `no_disponible`: it does not apply, or the turn's message budget is spent.
    * `error`: it failed.

    `decir` is a short instruction about what to say to the customer. `texto` is
    the prose the model reads - prose, not JSON, because a sentence it can quote
    produces fewer transcription errors than a structure it has to re-serialise
    (that is where an invented price comes from). `data` is the structured copy,
    for the trace and for tools that build on each other.
    """

    status: ToolStatus
    data: dict[str, Any] = field(default_factory=dict)
    decir: str | None = None
    texto: str | None = None

    @classmethod
    def hecho(
        cls,
        *,
        data: dict[str, Any] | None = None,
        decir: str | None = None,
        texto: str | None = None,
    ) -> ToolResult:
        return cls("hecho", data or {}, decir, texto)

    @classmethod
    def omitido(cls, *, decir: str | None = None, data: dict[str, Any] | None = None) -> ToolResult:
        return cls("omitido", data or {}, decir)

    @classmethod
    def no_disponible(
        cls,
        *,
        decir: str | None = None,
        texto: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls("no_disponible", data or {}, decir, texto)

    @classmethod
    def error(cls, decir: str) -> ToolResult:
        return cls("error", {}, decir)

    @property
    def ok(self) -> bool:
        return self.status != "error"

    def render(self) -> str:
        """The text the model reads. The status is always spelled out: platform
        rule 4 ("never say you did something unless the tool returned `hecho`")
        only works if the model can see it."""
        if self.status == "error":
            return f"ERROR: {self.decir or self.texto or 'la herramienta falló.'}"
        lines = [f"estado: {self.status}"]
        if self.texto:
            lines.append(self.texto)
        elif self.data:
            lines.append(json.dumps(self.data, ensure_ascii=False, default=str))
        if self.decir:
            lines.append(f"Qué decir al cliente: {self.decir}")
        return "\n".join(lines)


@dataclass
class TurnEnding:
    """Set by a tool that closes the turn (`derivar_a_humano`, TASK-443).

    `reply` is what the customer receives instead of the model's next words: the
    turn stops right after the tool, so the model never writes after a handover.
    """

    reply: str | None = None

    @property
    def ended(self) -> bool:
        return self.reply is not None


@dataclass(frozen=True)
class ToolContext:
    """Everything a handler may touch, already scoped to one tenant."""

    session: Session
    tenant_id: uuid.UUID
    # The platform provider, for tools that need embeddings. Deliberately not
    # the tenant's own chat credential: embeddings must stay on one model or
    # stored vectors stop being comparable.
    llm: LLMProvider
    conversation_id: uuid.UUID | None = None
    # Outbound messages this turn may still send (C-11).
    budget: TurnBudget = field(default_factory=TurnBudget)
    # The outbound adapter the sending tools use (`agent/outbound.py`). None
    # when the turn cannot send anything, which the tools answer as
    # `no_disponible`.
    adapter: TurnOutbound | None = None
    # The tenant's effective process options (paths, required data). Filled in
    # by `ToolCatalog.for_stage` from the stage's scope, so a handler always
    # reads the options its schema was built from.
    options: EffectiveOptions | None = None
    # In the simulator nothing leaves for the provider (C-19).
    sandbox: bool = False
    # What holds only for this turn: the tapped option, the receipt result.
    turn: TurnContext = field(default_factory=TurnContext)
    # The facts the stage was computed from. Read-only for tools.
    facts: Facts = field(default_factory=Facts)
    # Stored facts the tools set during the turn; the pipeline saves them.
    facts_patch: dict[str, Any] = field(default_factory=dict)
    # Most lines an order may have (services: 3). Filled by `ToolCatalog.for_stage`
    # from the template, like `options`.
    max_lines: int | None = None
    # Names the customer chose this turn that are not in the catalogue (a free theme
    # of a package). The catalogue guard accepts them in the reply, or it would
    # mistake "Excel avanzado" for an invented product.
    extra_names: list[str] = field(default_factory=list)
    # Where the business is told that something waits for a person (a cash-on-delivery
    # order, TASK-431). None in the simulator and wherever nobody is to be told.
    notices: OrderNotices | None = None
    # The published rubro version whose FAQs and objections `consultar_conocimiento`
    # also searches (017, TASK-461). None: no rubro, or none published.
    rubro_version_id: uuid.UUID | None = None
    # A tool that ends the turn writes here; the agent loop stops after it.
    ending: TurnEnding = field(default_factory=TurnEnding)
    # The option menus sent this turn. Shared by the copies `ToolCatalog.for_stage` makes, so
    # `mostrar_opciones` sends one menu per turn whatever stage scope runs it.
    options_this_turn: list[str] = field(default_factory=list)


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[str | ToolResult]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler
    # The tool changes something (an order, the customer's data): see `ToolDefinition`.
    effect: bool = False


@dataclass
class ToolRegistry:
    """The tools offered to the model for one turn."""

    _tools: dict[str, RegisteredTool] = field(default_factory=dict)
    # Old names that still resolve to a registered tool without being offered.
    _aliases: dict[str, str] = field(default_factory=dict)

    def register(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        effect: bool = False,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool {name!r} is already registered")
        self._tools[name] = RegisteredTool(
            spec=ToolSpec(name=name, description=description, parameters=parameters),
            handler=handler,
            effect=effect,
        )

    def alias(self, old_name: str, target: str) -> None:
        """Let `old_name` keep working as a call to `target`.

        The alias is not in `specs()` or `names()`: the model is offered only the
        current name, but a call that still uses the old one - from the history
        of a conversation, or a prompt written before the rename - is served.
        """
        if target not in self._tools:
            raise ValueError(f"cannot alias {old_name!r} to unregistered tool {target!r}")
        if old_name in self._tools:
            raise ValueError(f"{old_name!r} is a registered tool, not an alias")
        self._aliases[old_name] = target

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(self._aliases.get(name, name))

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def __len__(self) -> int:
        return len(self._tools)


# ------------------------------------------------------------- stage tools


@dataclass(frozen=True)
class ToolScope:
    """What decides which tools a turn gets and how their schema reads."""

    template: Template
    stage: Stage
    facts: Facts
    options: EffectiveOptions


@dataclass(frozen=True)
class ToolDefinition:
    """One tool of the catalogue: its text, its schema and its handler.

    `parameters` is a function of the scope because the schema is per tenant
    (the path keys, the data fields the template asks for) and per stage.
    `available` hides the tool altogether (`elegir_camino` without paths).
    `guard` refuses a call the stage does not allow even if the model makes it.
    `effect` marks a tool that writes (the order, the customer's data, a message):
    the agent loop's per-turn cache must not serve its answer after another write
    has happened (`ver` after `agregar` would otherwise show the old order).
    """

    name: str
    description: str
    parameters: Callable[[ToolScope], dict[str, Any]]
    handler: ToolHandler
    available: Callable[[ToolScope], bool] | None = None
    guard: Callable[[ToolScope, dict[str, Any]], ToolResult | None] | None = None
    effect: bool = False


# The tool the agent had before Feature 004 and the one that replaces it. A call
# to the old name is served by the new tool (`plan.md`, "Decisiones Posteriores").
LEGACY_ALIASES: dict[str, str] = {"consultar_catalogo": "buscar_productos"}


def tools_of_stage(scope: ToolScope) -> tuple[str, ...]:
    """The tool names of a stage: its own, then the global ones. No model turn
    (DERIVADO) means none."""
    if scope.stage is Stage.DERIVADO:
        return ()
    rejected = scope.facts.pago_estado == "rechazado"
    names = list(scope.template.stage(scope.stage).tools_for(rejected=rejected))
    names.extend(tool for tool in GLOBAL_TOOLS if tool not in names)
    if scope.options.agendar:
        names.extend(tool for tool in SCHEDULING_TOOL_NAMES if tool not in names)
    if scope.options.captar:
        names.extend(tool for tool in LEAD_TOOL_NAMES if tool not in names)
    if scope.options.postventa:
        names.extend(tool for tool in POSTSALE_TOOL_NAMES if tool not in names)
    if scope.options.cotizar:
        names.extend(tool for tool in QUOTE_TOOL_NAMES if tool not in names)
    return tuple(names)


class ToolCatalog:
    """Every tool the model can ever be given, by name."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name in self._definitions:
                raise ValueError(f"tool {definition.name!r} is defined twice")
            self._definitions[definition.name] = definition

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def get(self, name: str) -> ToolDefinition:
        return self._definitions[name]

    def for_stage(
        self, template: Template, stage: Stage, facts: Facts, options: EffectiveOptions
    ) -> ToolRegistry:
        """The registry of one turn.

        Each stage exposes only its own tools plus the global ones
        (`plantillas.md` section 2); the schema of each is adapted to the tenant
        and the guards are bound to this stage and these facts.
        """
        scope = ToolScope(template=template, stage=stage, facts=facts, options=options)
        registry = ToolRegistry()
        for name in tools_of_stage(scope):
            definition = self._definitions[name]
            if definition.available is not None and not definition.available(scope):
                continue
            registry.register(
                name=definition.name,
                description=definition.description,
                parameters=definition.parameters(scope),
                handler=_guarded(definition, scope),
                effect=definition.effect,
            )
        for old_name, target in LEGACY_ALIASES.items():
            if target in registry.names():
                registry.alias(old_name, target)
        return registry


def _guarded(definition: ToolDefinition, scope: ToolScope) -> ToolHandler:
    """Bind a definition to the scope of one turn.

    Runs the stage guard before the handler and hands the handler a context that
    carries the scope's options and the template's line limit. `replace` copies
    references, so the budget, `facts_patch` and `extra_names` the handler writes
    to are still the turn's own.
    """
    guard = definition.guard

    async def run(context: ToolContext, arguments: dict[str, Any]) -> str | ToolResult:
        if guard is not None:
            refusal = guard(scope, arguments)
            if refusal is not None:
                return refusal
        bound = replace(context, options=scope.options, max_lines=scope.template.max_lineas)
        return await definition.handler(bound, arguments)

    return run
