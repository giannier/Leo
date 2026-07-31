"""The tool registry the agent-core loops over.

A registry rather than a hardcoded `if name == ...` chain, because Features
003 and 004 add tools by the handful - media, vision, receipt validation - and
the loop must not need editing to accept them. Registering is the whole
integration: declare the schema, write the handler, and the model can call it.

Every handler receives a `ToolContext` carrying the tenant. That is not
convenience: it is what makes a tool unable to read outside its own business
even if its implementation forgets, because the repositories it is handed are
already bound.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.providers.llm.base import LLMProvider, ToolSpec


@dataclass(frozen=True)
class ToolContext:
    """Everything a handler may touch, already scoped to one tenant."""

    session: Session
    tenant_id: uuid.UUID
    # The platform provider, for tools that need embeddings. Deliberately not
    # the tenant's own chat credential: embeddings must stay on one model or
    # stored vectors stop being comparable.
    llm: LLMProvider


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass
class ToolRegistry:
    """The tools offered to the model for one turn."""

    _tools: dict[str, RegisteredTool] = field(default_factory=dict)

    def register(
        self, *, name: str, description: str, parameters: dict[str, Any], handler: ToolHandler
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool {name!r} is already registered")
        self._tools[name] = RegisteredTool(
            spec=ToolSpec(name=name, description=description, parameters=parameters),
            handler=handler,
        )

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def __len__(self) -> int:
        return len(self._tools)
