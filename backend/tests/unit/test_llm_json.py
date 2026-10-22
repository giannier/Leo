"""Reading a JSON answer out of what a model wrote (017)."""

from __future__ import annotations

import pytest

from app.learning.llm_json import parse_json


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"estilo": "tutean"}', {"estilo": "tutean"}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Claro, aquí está: {"a": [1, 2]} ¿algo más?', {"a": [1, 2]}),
        ('[{"titulo": "x"}, {"titulo": "y"}]', [{"titulo": "x"}, {"titulo": "y"}]),
        ('Resultado:\n[{"i": 0}]', [{"i": 0}]),
        ("[]", []),
    ],
)
def test_the_json_is_found_whatever_surrounds_it(text: str, expected: object) -> None:
    assert parse_json(text) == expected


@pytest.mark.parametrize("text", ["", "no hay json", "{rota", '{"a": }'])
def test_what_is_not_json_is_none(text: str) -> None:
    assert parse_json(text) is None
