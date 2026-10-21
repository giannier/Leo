"""What the agent does, and what follows from it (feature 019)."""

from __future__ import annotations

import pytest

from app.process.capabilities import derive_objective, derive_template, normalize


class TestNormalize:
    def test_attend_is_always_on_and_the_order_is_fixed(self) -> None:
        assert normalize(["cotizar"]) == ["atender", "cotizar"]
        assert normalize(["cotizar", "vender", "vender"]) == ["atender", "vender", "cotizar"]
        assert normalize(None) == ["atender"]

    def test_planned_capabilities_are_dropped_until_they_exist(self) -> None:
        assert normalize(["vender", "agendar", "captar"]) == ["atender", "vender"]
        assert normalize(["agendar"], only_available=False) == ["atender", "agendar"]

    def test_what_is_unknown_is_ignored(self) -> None:
        assert normalize(["vender", "inventada"]) == ["atender", "vender"]


class TestTheTemplateFollowsWhatTheAgentDoes:
    @pytest.mark.parametrize(
        ("capabilities", "suggested", "expected"),
        [
            (["atender", "vender"], None, "productos"),
            (["atender", "vender"], "productos", "productos"),
            (["atender", "vender", "cotizar"], "productos", "servicios"),
            (["atender", "cotizar"], None, "servicios"),
            (["atender"], "productos", "servicios"),
            (["atender", "vender"], "servicios", "servicios"),
            (["atender", "vender"], "certificaciones", "certificaciones"),
            (["atender", "vender", "cotizar"], "certificaciones", "certificaciones"),
            (["atender", "cotizar"], "certificaciones", "servicios"),
        ],
    )
    def test_the_table(self, capabilities: list[str], suggested: str | None, expected: str) -> None:
        assert derive_template(capabilities, suggested) == expected


class TestTheObjective:
    def test_it_sells_when_it_sells_or_quotes(self) -> None:
        assert derive_objective(["atender", "vender"]) == "ambos"
        assert derive_objective(["atender", "cotizar"]) == "ambos"

    def test_it_only_answers_when_it_does_nothing_else(self) -> None:
        assert derive_objective(["atender"]) == "atender"
