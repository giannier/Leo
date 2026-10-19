"""Reading a survey answer without the model (Feature 013, C-103)."""

from __future__ import annotations

import pytest

from app.services.analytics.csat import read_answer


@pytest.mark.parametrize(
    ("text", "score"),
    [
        ("5", 5),
        ("cinco", 5),
        ("Un 4", 4),
        ("⭐⭐⭐", 3),
        ("4 de 5", 4),
        ("5/5", 5),
        ("10/10", 5),
        ("Excelente!!", 5),
        ("muy buena atención", 5),
        ("bien", 4),
        ("malo", 2),
        ("Pésimo", 1),
    ],
)
def test_a_score_is_read(text: str, score: int) -> None:
    reading = read_answer(text)
    assert reading.score == score and not reading.for_assistant


@pytest.mark.parametrize("text", ["más o menos", "regular", "Hola, quiero otro pedido", "👍"])
def test_without_a_clear_score_it_goes_to_the_assistant(text: str) -> None:
    reading = read_answer(text)
    assert reading.score is None and reading.for_assistant and reading.comment == text


def test_a_score_with_a_question_is_both() -> None:
    reading = read_answer("5, ¿tienen torta de chocolate?")
    assert reading.score == 5 and reading.for_assistant
    assert reading.comment is not None and "torta" in reading.comment
