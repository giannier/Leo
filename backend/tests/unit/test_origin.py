"""Where a customer came from: names a person reads in the CRM (feature 019, phase F)."""

from __future__ import annotations

import pytest

from app.services.origin import normalize_origin, origin_from_text


class TestTheName:
    @pytest.mark.parametrize(
        ("written", "shown"),
        [
            ("ig", "Instagram"),
            ("INSTAGRAM", "Instagram"),
            ("fb", "Facebook"),
            ("tik tok", "TikTok"),
            ("yt", "YouTube"),
            ("pagina web", "Página web"),
            ("mi radio local", "Mi radio local"),
            ("  Feria 2026!! ", "Feria 2026"),
        ],
    )
    def test_the_known_ones_and_a_cleaned_unknown(self, written: str, shown: str) -> None:
        assert normalize_origin(written) == shown

    @pytest.mark.parametrize("nothing", [None, "", "   ", "!!!"])
    def test_nothing_usable_is_none(self, nothing: str | None) -> None:
        assert normalize_origin(nothing) is None

    def test_it_is_bounded(self) -> None:
        assert len(normalize_origin("x" * 200) or "") == 40


class TestWhatTheCustomerSays:
    @pytest.mark.parametrize(
        ("said", "origin"),
        [
            ("Hola, te escribo desde Instagram", "Instagram"),
            ("vengo de tiktok, ¿qué precio tiene?", "TikTok"),
            ("vi tu publicación en facebook", "Facebook"),
            ("los vi en Google", "Google"),
            ("hola vengo de la página web", "Página web"),
            ("Te escribo desde tu página", "Página web"),
        ],
    )
    def test_the_first_words_that_name_a_network(self, said: str, origin: str) -> None:
        assert origin_from_text(said) == origin

    @pytest.mark.parametrize(
        "said",
        [
            "Hola quiero precios",
            "Desde cuándo atienden",
            "vengo de Lima",
            "",
            "me interesa el plan",
        ],
    )
    def test_ordinary_sentences_name_nothing(self, said: str) -> None:
        assert origin_from_text(said) is None
