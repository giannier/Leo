"""The system prompt is assembled from the tenant's own rows (TASK-014).

"Configuración sobre código": a new industry must never need a code change.
These cases pin that the builder reads the database and nothing else, and that
the "no inventar" guardrail is always present - it is the one instruction that
must survive every combination of settings.
"""

from __future__ import annotations

import pytest

from app.agent.prompts.builder import build_system_prompt
from app.models.business import AgentConfig, BusinessProfile


def _profile(**overrides: object) -> BusinessProfile:
    profile = BusinessProfile()
    profile.business_name = "Panaderia Quispe"
    profile.industry = "restaurante"
    profile.description = None
    profile.currency = "PEN"
    profile.timezone = "America/Lima"
    profile.opening_hours = {}
    profile.social_links = {}
    profile.website = None
    for key, value in overrides.items():
        setattr(profile, key, value)
    return profile


def _config(**overrides: object) -> AgentConfig:
    config = AgentConfig()
    config.agent_name = "Leo"
    config.tone = "cercano"
    config.objective = "ambos"
    config.restrictions = None
    config.escalation_rules = []
    config.farewell_on_pause = None
    config.is_active = False
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class TestIdentity:
    def test_the_agent_and_business_are_named(self) -> None:
        prompt = build_system_prompt(profile=_profile(), config=_config(agent_name="Rita"))
        assert "Rita" in prompt
        assert "Panaderia Quispe" in prompt

    def test_the_industry_is_stated(self) -> None:
        prompt = build_system_prompt(profile=_profile(industry="salud"), config=_config())
        assert "salud" in prompt

    def test_the_owners_description_is_included(self) -> None:
        prompt = build_system_prompt(
            profile=_profile(description="Panaderia familiar desde 1998"), config=_config()
        )
        assert "1998" in prompt

    def test_it_works_with_nothing_configured(self) -> None:
        """A tenant that has not opened the wizard still gets a usable agent,
        because the sandbox has to be reachable before the wizard is finished."""
        prompt = build_system_prompt(profile=None, config=None)
        assert "Leo" in prompt
        assert "REGLAS QUE NO PUEDES ROMPER" in prompt


class TestGuardrail:
    """The load-bearing instruction. An agent that invents a price does more
    damage than one that says it does not know."""

    @pytest.mark.parametrize("tone", ["profesional", "cercano", "juvenil"])
    @pytest.mark.parametrize("objective", ["vender", "atender", "ambos"])
    def test_it_survives_every_combination(self, tone: str, objective: str) -> None:
        prompt = build_system_prompt(
            profile=_profile(), config=_config(tone=tone, objective=objective)
        )
        assert "Nunca inventes" in prompt

    def test_the_tools_are_named_as_the_only_source_of_truth(self) -> None:
        prompt = build_system_prompt(
            profile=_profile(),
            config=_config(),
            tool_names=("consultar_catalogo", "consultar_conocimiento"),
        )
        assert "consultar_catalogo" in prompt
        assert "única fuente de verdad" in prompt

    def test_there_is_a_permitted_way_to_fail(self) -> None:
        """A rule with no escape hatch is one the model routes around by
        inventing. The prompt must offer escalation as the alternative."""
        prompt = build_system_prompt(profile=_profile(), config=_config())
        assert "derivar" in prompt
        assert "Es mejor no saber que equivocarse" in prompt


class TestToneAndObjective:
    @pytest.mark.parametrize(
        ("tone", "marker"),
        [("profesional", "usted"), ("cercano", "Cálido"), ("juvenil", "Relajado")],
    )
    def test_each_tone_changes_the_instruction(self, tone: str, marker: str) -> None:
        prompt = build_system_prompt(profile=_profile(), config=_config(tone=tone))
        assert marker in prompt

    def test_atender_does_not_push_a_sale(self) -> None:
        prompt = build_system_prompt(profile=_profile(), config=_config(objective="atender"))
        assert "No presiones para vender" in prompt


class TestOwnerRules:
    def test_restrictions_are_passed_through(self) -> None:
        prompt = build_system_prompt(
            profile=_profile(), config=_config(restrictions="No ofrecer descuentos")
        )
        assert "No ofrecer descuentos" in prompt

    def test_escalation_triggers_are_phrased_readably(self) -> None:
        prompt = build_system_prompt(
            profile=_profile(),
            config=_config(escalation_rules=["cliente_molesto", "pide_humano"]),
        )
        assert "molesto" in prompt
        assert "hablar con una persona" in prompt

    def test_an_unknown_trigger_is_passed_through_not_dropped(self) -> None:
        """The column is JSONB and a later feature may add triggers this file
        has not heard of. Dropping one would remove a guardrail the owner
        switched on."""
        prompt = build_system_prompt(
            profile=_profile(), config=_config(escalation_rules=["pide_factura"])
        )
        assert "pide_factura" in prompt


class TestOpeningHours:
    def test_hours_are_rendered_in_spanish(self) -> None:
        prompt = build_system_prompt(
            profile=_profile(opening_hours={"mon": [["09:00", "18:00"]]}), config=_config()
        )
        assert "lunes: 09:00 a 18:00" in prompt

    def test_malformed_hours_do_not_break_the_agent(self) -> None:
        """This comes from a wizard form. One bad row must not take the agent
        down - it would fail at answer time, in front of a customer."""
        prompt = build_system_prompt(
            profile=_profile(opening_hours={"lunes": "todo el dia", "mon": [["09:00"]]}),
            config=_config(),
        )
        assert "REGLAS QUE NO PUEDES ROMPER" in prompt

    def test_no_hours_means_no_hours_line(self) -> None:
        prompt = build_system_prompt(profile=_profile(opening_hours={}), config=_config())
        assert "Horario de atención" not in prompt


class TestDeterminism:
    def test_the_same_rows_produce_the_same_prompt(self) -> None:
        """What lets TASK-016's evals compare prompts across changes."""
        first = build_system_prompt(profile=_profile(), config=_config())
        second = build_system_prompt(profile=_profile(), config=_config())
        assert first == second
