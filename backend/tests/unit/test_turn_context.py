"""What holds only for one turn (TASK-414, `plan.md`)."""

from __future__ import annotations

from app.process.turn import IMAGE_RECEIVED_LINE, TurnContext


def test_an_empty_context_has_no_lines() -> None:
    context = TurnContext()

    assert context.lines() == []
    assert context.is_empty()


def test_the_tapped_option_comes_first_then_the_receipt_result() -> None:
    context = TurnContext(opcion="El cliente eligió: Paquete 5", comprobante="comprobante recibido")

    assert context.lines() == ["El cliente eligió: Paquete 5", "comprobante recibido"]


def test_an_unanalysed_image_is_announced() -> None:
    assert TurnContext(imagen_recibida=True).lines() == [IMAGE_RECEIVED_LINE]


def test_the_receipt_result_replaces_the_generic_image_line() -> None:
    context = TurnContext(imagen_recibida=True, comprobante="la imagen NO es un comprobante")

    assert context.lines() == ["la imagen NO es un comprobante"]


def test_every_line_is_a_single_short_line() -> None:
    context = TurnContext(opcion="uno\n\ndos " + "x" * 500)

    (line,) = context.lines()

    assert "\n" not in line
    assert len(line) <= 300
