"""Every stage names only the tools it offers (audit of 2026-10-26).

The platform rules and the stage texts are written once, but each stage offers its own tools: rule
1 told the model to use buscar_productos in DATOS and COBRO, where there is no such tool, and rule
7 to apply a promotion with gestionar_pedido where it is not offered. A model told to use a tool it
does not have improvises. This walks every template, every stage the engine can reach, with and
without every capability, and fails when a prompt names a tool its stage does not offer.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from app.agent.prompts.stage import build_stage_prompt
from app.agent.tools.definitions import default_catalog, registry_for_stage
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from tests.unit.test_stage_prompt import NOW, _stage_facts, make_config

TOOLS = sorted(default_catalog().names(), key=len, reverse=True)
NAMED = re.compile(r"\b(" + "|".join(TOOLS) + r")\b")
CAPABILITIES = {
    "cotizar": True,
    "datos_cotizacion": ("cantidad", "fecha"),
    "captar": True,
    "agendar": True,
    "postventa": True,
}
# Mentions that do not ask the model to use the tool: a prohibition, and a step that only applies
# when the business set the paths that also offer the tool.
ALLOWED = {
    "no lo pidas con gestionar_pedido",
    "Si lo anunciado corresponde a un camino, llama a elegir_camino",
}


def _reachable(stage: Stage, has_paths: bool) -> bool:
    if stage is Stage.DERIVADO:
        return False  # a person has the conversation: no model is called
    return has_paths or stage not in (Stage.CALIFICAR, Stage.ASESORIA)


@pytest.mark.parametrize("template_key", sorted(TEMPLATES))
@pytest.mark.parametrize("with_capabilities", [False, True])
def test_no_stage_names_a_tool_it_does_not_offer(
    template_key: str, with_capabilities: bool
) -> None:
    template = TEMPLATES[template_key]
    base = make_config(template_key, full=True)
    config = (
        replace(base, options=replace(base.options, **CAPABILITIES)) if with_capabilities else base
    )
    wrong: list[str] = []
    for stage in Stage:
        if not _reachable(stage, bool(config.options.caminos)):
            continue
        facts = _stage_facts(stage)
        registry = registry_for_stage(template, stage, facts, config.options)
        offered = set(registry.names())
        text = build_stage_prompt(config, facts, stage, now=NOW, tool_specs=registry.specs()).text
        for allowed in ALLOWED:
            text = text.replace(allowed, "")
        missing = sorted(set(NAMED.findall(text)) - offered)
        if missing:
            wrong.append(f"{stage.value}: {', '.join(missing)}")

    assert not wrong, f"{template_key} names tools its stages do not offer: {wrong}"


def test_a_stage_with_the_tool_still_names_it() -> None:
    template = TEMPLATES["productos"]
    config = make_config("productos", full=True)
    facts = _stage_facts(Stage.IDENTIFICAR)
    registry = registry_for_stage(template, Stage.IDENTIFICAR, facts, config.options)

    text = build_stage_prompt(
        config, facts, Stage.IDENTIFICAR, now=NOW, tool_specs=registry.specs()
    ).text

    assert "búscala antes con buscar_productos" in text
    assert 'gestionar_pedido(accion="aplicar_promocion")' in text
