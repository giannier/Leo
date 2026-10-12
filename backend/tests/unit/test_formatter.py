"""The WhatsApp formatter (TASK-416, port of *Formato WhatsApp1* of Capacita v21).

The first seven tests are the seven cases of `harness21.js`, with the same texts
and the same expectations.
"""

from __future__ import annotations

import pytest

from app.agent.budget import TurnBudget
from app.agent.formatter import strip_internal, to_bubbles, to_whatsapp_markup


def _paragraph(times: int) -> str:
    return ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * times).strip()


LONG = "\n\n".join([_paragraph(4)] * 4)


def _fresh() -> TurnBudget:
    return TurnBudget()


# ------------------------------------------------- the 7 cases of harness21.js


def test_v21_short_answer_is_one_message_with_bold_converted() -> None:
    bubbles = to_bubbles("¡Hola **Ana**! 😊 ¿Qué tema necesitas certificar?", "moderado", _fresh())

    assert len(bubbles) == 1
    assert "*Ana*" in bubbles[0]
    assert "**" not in bubbles[0]


def test_v21_long_answer_is_two_messages() -> None:
    bubbles = to_bubbles(LONG, "moderado", _fresh())

    assert len(bubbles) == 2
    assert len(bubbles[0]) > 200
    assert len(bubbles[1]) > 200


def test_v21_with_a_single_bubble_allowed_it_never_splits() -> None:
    budget = TurnBudget(max=1, reserva_texto=1)

    assert len(to_bubbles(LONG, "moderado", budget)) == 1


def test_v21_long_text_without_paragraphs_is_not_cut_mid_sentence() -> None:
    bubbles = to_bubbles(_paragraph(30), "moderado", _fresh())

    assert len(bubbles) == 1


def test_v21_internal_notes_and_json_are_removed() -> None:
    raw = (
        "No responder: mensaje vacío del sistema\n"
        "¡Listo Ana! Te envié las cuentas.\n"
        '```json\n{"x":1}\n```'
    )

    assert to_bubbles(raw, "moderado", _fresh()) == ["¡Listo Ana! Te envié las cuentas."]


def test_v21_lists_and_links_use_whatsapp_format() -> None:
    raw = "Tienes:\n- Gestión Pública\n- SIAF\n\nVer [tienda](https://t.co/x)"

    (bubble,) = to_bubbles(raw, "moderado", _fresh())

    assert "• Gestión Pública" in bubble
    assert "tienda: https://t.co/x" in bubble


def test_v21_empty_answer_sends_nothing() -> None:
    assert to_bubbles("", "moderado", _fresh()) == []


# ----------------------------------------------------------- detail and budget


def test_breve_never_splits_even_when_long() -> None:
    assert len(to_bubbles(LONG, "breve", _fresh())) == 1


def test_moderado_stays_in_one_bubble_up_to_the_threshold() -> None:
    text = "\n\n".join(["a" * 300, "b" * 300])  # 602 characters

    assert len(to_bubbles(text, "moderado", _fresh())) == 1


def test_moderado_splits_in_two_over_the_threshold() -> None:
    text = "\n\n".join(["a" * 400, "b" * 400])

    assert to_bubbles(text, "moderado", _fresh()) == ["a" * 400, "b" * 400]


def test_extenso_uses_at_most_two_bubbles() -> None:
    text = "\n\n".join(["a" * 500] * 6)

    assert len(to_bubbles(text, "extenso", _fresh())) == 2


def test_an_unknown_detail_behaves_like_moderado() -> None:
    assert len(to_bubbles(LONG, "otro", _fresh())) == 2


def test_after_two_tool_messages_the_text_goes_out_in_one_bubble() -> None:
    budget = TurnBudget()
    budget.consume()
    budget.consume()

    assert len(to_bubbles(LONG, "extenso", budget)) == 1


def test_after_one_tool_message_the_text_may_still_split() -> None:
    budget = TurnBudget()
    budget.consume()

    assert len(to_bubbles(LONG, "extenso", budget)) == 2


def test_an_exhausted_budget_sends_no_text() -> None:
    budget = TurnBudget(max=1, reserva_texto=0)
    budget.consume()

    assert to_bubbles("Hola", "moderado", budget) == []


def test_the_cut_is_the_paragraph_break_closest_to_the_middle() -> None:
    text = "\n\n".join(["a" * 100, "b" * 100, "c" * 600, "d" * 100])

    first, second = to_bubbles(text, "moderado", _fresh())

    # The text is 906 characters: cutting after "b" lands 249 from the middle,
    # after "a" 351 and after "c" 353.
    assert first == "\n\n".join(["a" * 100, "b" * 100])
    assert second == "\n\n".join(["c" * 600, "d" * 100])


def test_a_custom_threshold_is_honoured() -> None:
    text = "\n\n".join(["a" * 60, "b" * 60])

    assert len(to_bubbles(text, "moderado", _fresh(), threshold=100)) == 2


# --------------------------------------------------------------- the markup


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Total: **S/ 9.00**", "Total: *S/ 9.00*"),
        ("***muy importante***", "*muy importante*"),
        ("Total: __S/ 9.00__", "Total: *S/ 9.00*"),
        ("~~agotado~~", "~agotado~"),
        ("### Pedido\nLeche", "*Pedido*\nLeche"),
        ("_importante_", "_importante_"),
        ("2 ** 3 son 8", "2 ** 3 son 8"),
        ("#42 es tu número", "#42 es tu número"),
        ("- uno\n+ dos\n* tres", "• uno\n• dos\n• tres"),
        ("> cita", "cita"),
        ("usa `codigo` aquí", "usa codigo aquí"),
        ("Leche: S/ 3.00   \nQueso: S/ 16.00", "Leche: S/ 3.00\nQueso: S/ 16.00"),
        ("Hola\n\n\n\nChao", "Hola\n\nChao"),
        ("   ", ""),
        ("*ya en formato*", "*ya en formato*"),
    ],
)
def test_markdown_becomes_whatsapp_markup(raw: str, expected: str) -> None:
    assert to_whatsapp_markup(raw) == expected


@pytest.mark.parametrize(
    "note",
    ["no responder", "No es interacción", "Mensaje vacío", "mensaje no es consulta", "[interno] x"],
)
def test_every_internal_note_marker_is_removed(note: str) -> None:
    assert strip_internal(f"{note}\nHola") == "Hola"


def test_a_note_in_the_middle_of_a_sentence_is_kept() -> None:
    assert strip_internal("Te digo: no responder así está mal") == (
        "Te digo: no responder así está mal"
    )


def test_a_reply_that_is_only_a_note_sends_nothing() -> None:
    assert to_bubbles("nota interna: nada que decir", "moderado", _fresh()) == []
