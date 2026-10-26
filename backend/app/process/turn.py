"""What is true only for this turn (Feature 004, `plan.md`).

`TurnContext` carries the things the model must know *now* but that are not worth
keeping: the option the customer just tapped, what the vision step made of an
image, the fact that an image arrived. It is never persisted; the prompt builder
shows it under "ESTE TURNO" in the state block and it is gone on the next turn.

The pipeline (TASK-421) builds it from the inbound message; tools receive it in
their context (TASK-417).
"""

from __future__ import annotations

from dataclasses import dataclass

IMAGE_RECEIVED_LINE = "[El cliente envió una imagen]"
# The owner edited a product while this conversation went on, and the model only remembers what it
# said before, not what the tools answered: told, it searches again instead of repeating it.
CATALOGUE_CHANGED_LINE = (
    "El catálogo cambió desde tu último mensaje: lo que dijiste antes de productos, precios o "
    "condiciones puede estar desactualizado. Vuelve a consultar con buscar_productos antes de "
    "repetirlo."
)
_MAX_LINE = 300


def _one_line(text: str) -> str:
    """Collapse a text to a single short line: it ends up inside the prompt."""
    return " ".join(text.split())[:_MAX_LINE]


@dataclass(frozen=True)
class TurnContext:
    """One-turn facts for the "ESTE TURNO" block."""

    # The context line of a tap (`InteractiveOutcome.context`).
    opcion: str | None = None
    # The outcome of the receipt pipeline, already worded for the model:
    # "la imagen NO es un comprobante" or "comprobante recibido: S/ 500, BCP, op. 123".
    comprobante: str | None = None
    # An image arrived and was not analysed (C-16).
    imagen_recibida: bool = False
    # A product changed after the assistant last spoke here: what it said of it may be out of date.
    catalogo_cambiado: bool = False

    def lines(self) -> list[str]:
        """The lines to show, in the order the customer did things."""
        lines: list[str] = []
        if self.opcion:
            lines.append(_one_line(self.opcion))
        if self.comprobante:
            lines.append(_one_line(self.comprobante))
        elif self.imagen_recibida:
            lines.append(IMAGE_RECEIVED_LINE)
        if self.catalogo_cambiado:
            lines.append(CATALOGUE_CHANGED_LINE)
        return lines

    def is_empty(self) -> bool:
        return not self.lines()
