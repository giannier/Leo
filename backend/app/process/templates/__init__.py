"""The process templates, by key (Feature 004).

A tenant picks one with `agent_configs.process_template_key`. They live in code
and carry a `version`, so a conversation can keep the rules it started with when
a template changes (`conversation_state.template_version`).
"""

from __future__ import annotations

from app.process.templates import certificaciones, productos, servicios, universal
from app.process.templates.base import (
    GLOBAL_TOOLS,
    KNOWN_PLACEHOLDERS,
    LEAD_TOOL_NAMES,
    POSTSALE_TOOL_NAMES,
    QUOTE_TOOL_NAMES,
    SCHEDULING_TOOL_NAMES,
    TOOL_NAMES,
    EffectiveOptions,
    Stage,
    StageSpec,
    Template,
)

TEMPLATES: dict[str, Template] = {
    template.key: template
    for template in (
        productos.TEMPLATE,
        certificaciones.TEMPLATE,
        servicios.TEMPLATE,
        universal.TEMPLATE,
    )
}

# The key of the universal process (feature 020).
UNIVERSAL = universal.TEMPLATE.key


def get_template(key: str) -> Template:
    """The template with this key. Raises `KeyError` for one that does not exist."""
    try:
        return TEMPLATES[key]
    except KeyError:
        raise KeyError(f"unknown process template {key!r}; known: {sorted(TEMPLATES)}") from None


__all__ = [
    "GLOBAL_TOOLS",
    "LEAD_TOOL_NAMES",
    "POSTSALE_TOOL_NAMES",
    "QUOTE_TOOL_NAMES",
    "SCHEDULING_TOOL_NAMES",
    "KNOWN_PLACEHOLDERS",
    "TEMPLATES",
    "TOOL_NAMES",
    "UNIVERSAL",
    "EffectiveOptions",
    "Stage",
    "StageSpec",
    "Template",
    "get_template",
]
