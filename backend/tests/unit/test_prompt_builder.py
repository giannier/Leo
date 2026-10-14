"""The system prompt is assembled from the tenant's own rows (TASK-014).

"Configuración sobre código": a new industry must never need a code change.
These cases pin that the builder reads the database and nothing else, and that
the "no inventar" guardrail is always present - it is the one instruction that
must survive every combination of settings.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent.prompts.builder import (
    MAX_CORRECTION_NAMES,
    build_name_correction,
    build_system_prompt,
)
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

    def test_product_names_must_be_reproduced_as_the_tool_returned_them(self) -> None:
        """Forbidding invention was not enough: the catalogue said "Leche" and
        the agent offered "Leche pausterizada". A name that is almost right is
        a product the business does not sell, so the rule now says so."""
        prompt = build_system_prompt(profile=_profile(), config=_config())
        assert "tal cual te lo devolvió la herramienta" in prompt

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


class TestTheNameCorrection:
    """What a model that renamed a product is handed before its one retry.

    "No inventes productos" is already rule 1 of the system prompt and the
    model broke it, so repeating it changes nothing. The correction works by
    being concrete: the exact string it wrote, and the exact strings it may
    copy instead.
    """

    def test_it_names_the_offence_and_the_real_names(self) -> None:
        correction = build_name_correction(["leche pausterizada"], ["Leche", "Yogurt griego"])

        assert "«leche pausterizada»" in correction
        assert "- Leche" in correction
        assert "- Yogurt griego" in correction

    def test_it_is_written_in_spanish_like_the_rest_of_the_prompt(self) -> None:
        """Correcting in one language what must be rewritten in another
        measurably degrades adherence."""
        correction = build_name_correction(["leche pausterizada"], ["Leche"])

        assert correction.startswith("ALTO.")
        assert "derivar con una persona del negocio" in correction

    def test_the_mangled_name_survives_a_truncated_catalogue(self) -> None:
        """A catalogue can hold thousands and the whole list would cost more
        than the turn it is fixing. The name the model actually mangled must
        not be the one dropped, or the correction says nothing useful."""
        catalogue = [f"Producto {index}" for index in range(200)] + ["Leche"]

        correction = build_name_correction(["leche pausterizada"], catalogue)

        assert "\n- Leche\n" in correction
        assert f"de {len(catalogue)}" in correction
        assert correction.count("\n- ") == MAX_CORRECTION_NAMES

    def test_a_short_catalogue_is_quoted_whole_without_a_caveat(self) -> None:
        correction = build_name_correction(["leche entera"], ["Leche", "Pan"])

        assert "Son solo los primeros" not in correction


class TestToday:
    """The agent could not reason about dates at all: asked for "el 5" it
    answered "sí, se puede" and then quoted a two-day rule that ruled the date
    out, in the same sentence. It had the policy and no idea what day it was.
    """

    def test_todays_date_is_stated_in_the_tenants_timezone(self) -> None:
        # 03:30 UTC on the 5th is still the 4th in Lima. The tenant's own
        # timezone decides, not the server's.
        prompt = build_system_prompt(
            profile=_profile(timezone="America/Lima"),
            config=_config(),
            now=datetime(2026, 8, 5, 3, 30, tzinfo=UTC),
        )
        assert "martes 4 de agosto de 2026" in prompt

    def test_a_different_timezone_moves_the_date(self) -> None:
        prompt = build_system_prompt(
            profile=_profile(timezone="Asia/Tokyo"),
            config=_config(),
            now=datetime(2026, 8, 5, 3, 30, tzinfo=UTC),
        )
        assert "miércoles 5 de agosto de 2026" in prompt

    def test_an_unknown_timezone_falls_back_instead_of_raising(self) -> None:
        """The column is filled from a dropdown, but a bad value must not take
        the agent down in front of a customer."""
        prompt = build_system_prompt(
            profile=_profile(timezone="Marte/Olympus"),
            config=_config(),
            now=datetime(2026, 8, 5, 3, 30, tzinfo=UTC),
        )
        assert "martes 4 de agosto de 2026" in prompt

    def test_without_a_clock_there_is_no_date_line(self) -> None:
        """Callers that pin a prompt for comparison pass no clock at all."""
        assert "Hoy es" not in build_system_prompt(profile=_profile(), config=_config())

    def test_the_same_clock_still_produces_the_same_prompt(self) -> None:
        """Purity is what lets the evals compare prompts across changes, so the
        date may only enter through the injected clock."""
        now = datetime(2026, 8, 5, 3, 30, tzinfo=UTC)
        first = build_system_prompt(profile=_profile(), config=_config(), now=now)
        second = build_system_prompt(profile=_profile(), config=_config(), now=now)
        assert first == second


class TestWhatsAppFormatting:
    def test_the_prompt_asks_for_whatsapp_markup_not_markdown(self) -> None:
        """`**negrita**` reached the customer with the asterisks showing."""
        prompt = build_system_prompt(profile=_profile(), config=_config())
        assert "*negrita*" in prompt
        assert "Nunca uses **" in prompt

    def test_it_is_present_even_with_nothing_configured(self) -> None:
        assert "*negrita*" in build_system_prompt(profile=None, config=None)


class TestActionGuardrail:
    """Rule 1 forbids inventing data. This forbids inventing *actions* - the
    agent told a customer "queda registrado" for an order no table ever
    received, and she left believing it existed.
    """

    def test_the_agent_is_told_it_cannot_register_anything(self) -> None:
        prompt = build_system_prompt(profile=_profile(), config=_config())
        assert "No puedes registrar" in prompt

    def test_it_is_given_the_permitted_alternative(self) -> None:
        """A rule with no escape hatch is one the model routes around."""
        prompt = build_system_prompt(profile=_profile(), config=_config())
        assert "una persona del negocio lo confirmará" in prompt

    def test_it_survives_every_combination_of_settings(self) -> None:
        for tone in ("profesional", "cercano", "juvenil"):
            for objective in ("vender", "atender", "ambos"):
                prompt = build_system_prompt(
                    profile=None, config=_config(tone=tone, objective=objective)
                )
                assert "No puedes registrar" in prompt
