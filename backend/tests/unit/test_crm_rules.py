"""Where the sales process puts a conversation on the board (Feature 012, C-102)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.crm import actor_for, semantic_for


def _order(estado: str) -> Any:
    return SimpleNamespace(estado=estado)


@pytest.mark.parametrize(
    ("stage", "facts", "order", "expected"),
    [
        ("INICIO", {}, None, None),
        ("CALIFICAR", {}, None, None),
        ("ANUNCIO", {}, None, None),
        # "Hola, ¿horario?" is not a lead: interest in products is.
        ("IDENTIFICAR", {}, None, None),
        ("IDENTIFICAR", {"vio_productos": True}, None, "nuevo"),
        ("PEDIDO", {}, _order("borrador"), "interesado"),
        ("DATOS", {}, _order("borrador"), "interesado"),
        ("ASESORIA", {}, None, "interesado"),
        ("COBRO", {}, _order("por_confirmar"), "por_pagar"),
        ("VERIFICACION", {}, _order("por_confirmar"), "por_pagar"),
        ("CERRADO", {}, _order("confirmado"), "ganado"),
        ("CERRADO", {}, None, None),
        # A confirmed order wins whatever the stage says.
        ("DERIVADO", {}, _order("completado"), "ganado"),
        ("DERIVADO", {}, None, None),
        # Handed to an advisor for a quote: interested. Handed over for any other reason: no lead.
        ("DERIVADO", {"asesoria_derivada_en": "2026-10-20T20:00:00+00:00"}, None, "interesado"),
        ("DERIVADO", {"derivado": {"motivo": "queja"}}, None, None),
    ],
)
def test_the_map(stage: str, facts: dict[str, Any], order: Any, expected: str | None) -> None:
    assert semantic_for(stage, facts, order) == expected


def test_who_moved_it() -> None:
    assert actor_for("turno") == "agent"
    assert actor_for("herramienta:gestionar_pedido") == "agent"
    assert actor_for("operador:confirmar") == "system"
    assert actor_for("pedido_descartado") == "system"
