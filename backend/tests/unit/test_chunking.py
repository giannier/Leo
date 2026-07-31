"""The chunking strategy (TASK-012).

Pure functions, so these pin the actual retrieval behaviour rather than a
mocked approximation of it: chunk size bounds the embedding request, and
losing a sentence at a boundary is a silent recall failure nobody notices.
"""

from __future__ import annotations

import pytest

from app.services.chunking import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    chunk_text,
    normalise,
)


class TestNothingToIndex:
    @pytest.mark.parametrize("text", ["", "   ", "\n\n\t\n", " "])
    def test_blank_text_produces_no_chunks(self, text: str) -> None:
        """An empty list is how the worker learns there is nothing to index.

        One chunk of whitespace would embed to noise and the source would be
        reported `indexed` while the agent knows nothing.
        """
        assert chunk_text(text) == []


class TestNormalisation:
    def test_collapses_the_whitespace_pdf_extraction_leaves(self) -> None:
        raw = "Horario   de  atencion   \n\n\n\n  Lunes a viernes  \n"
        assert normalise(raw) == "Horario de atencion\n\nLunes a viernes"

    def test_windows_line_endings_become_paragraphs_not_doubles(self) -> None:
        assert normalise("uno\r\n\r\ndos") == "uno\n\ndos"

    def test_normalisation_shrinks_the_billable_character_count(self) -> None:
        """`char_count` is billed against the plan, so padding must not count."""
        raw = "a" + " " * 500 + "b"
        assert len(normalise(raw)) == 3


class TestSizing:
    def test_short_text_is_a_single_chunk(self) -> None:
        assert chunk_text("Abrimos de 9 a 18.") == ["Abrimos de 9 a 18."]

    def test_no_chunk_exceeds_the_target_plus_overlap(self) -> None:
        text = "\n\n".join(f"Parrafo {index}. " + "palabra " * 60 for index in range(40))
        chunks = chunk_text(text, max_chars=500, overlap=50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 500 + 50 for chunk in chunks)

    def test_defaults_stay_inside_the_embedding_models_token_window(self) -> None:
        """1000 + 150 chars is ~330 tokens; the model accepts 512."""
        assert DEFAULT_CHUNK_CHARS + DEFAULT_OVERLAP_CHARS < 512 * 3

    def test_a_sentence_longer_than_a_chunk_is_hard_split(self) -> None:
        sentence = "palabra " * 200  # one 1600-char "sentence", no full stop
        chunks = chunk_text(sentence, max_chars=200, overlap=0)
        assert len(chunks) > 1
        assert all(len(chunk) <= 200 for chunk in chunks)

    def test_hard_split_does_not_slice_a_word_in_half(self) -> None:
        chunks = chunk_text("palabra " * 200, max_chars=200, overlap=0)
        rejoined = " ".join(chunks).split()
        assert set(rejoined) == {"palabra"}

    def test_a_run_with_no_spaces_is_still_bounded(self) -> None:
        """A URL or a base64 blob has no word boundary to cut at."""
        chunks = chunk_text("x" * 900, max_chars=100, overlap=0)
        assert all(len(chunk) <= 100 for chunk in chunks)
        assert "".join(chunks) == "x" * 900


class TestStructureIsPreserved:
    def test_paragraphs_are_kept_whole_when_they_fit(self) -> None:
        """Half a paragraph embeds to something that means neither half."""
        text = "Delivery: S/ 5 en Miraflores.\n\nHorario: 12:00 a 22:00."
        assert chunk_text(text, max_chars=1000, overlap=0) == [text]

    def test_a_paragraph_that_does_not_fit_breaks_on_sentences(self) -> None:
        text = "Primera oracion completa. Segunda oracion completa. Tercera oracion."
        chunks = chunk_text(text, max_chars=40, overlap=0)
        assert all(chunk.endswith((".", "e")) for chunk in chunks)
        assert "Primera oracion completa." in chunks[0]

    def test_every_word_of_the_source_survives_chunking(self) -> None:
        """Whatever the strategy, no content may be dropped."""
        text = "\n\n".join(f"Item {index} con su descripcion." for index in range(50))
        chunks = chunk_text(text, max_chars=120, overlap=0)
        assert " ".join(chunks).split() == text.split()


class TestOverlap:
    def test_consecutive_chunks_share_their_boundary(self) -> None:
        """Without this, an answer straddling a boundary is unretrievable."""
        text = "\n\n".join(f"Parrafo numero {index} del documento." for index in range(20))
        chunks = chunk_text(text, max_chars=120, overlap=40)
        assert len(chunks) > 2
        for previous, current in zip(chunks[:-1], chunks[1:], strict=True):
            tail_words = set(previous.split()[-6:])
            assert tail_words & set(current.split()[:8])

    def test_the_overlap_never_opens_mid_word(self) -> None:
        text = "\n\n".join(f"Parrafo numero {index} del documento." for index in range(20))
        for chunk in chunk_text(text, max_chars=120, overlap=40):
            first = chunk.split()[0]
            assert first in text.split()

    def test_zero_overlap_is_allowed(self) -> None:
        text = "\n\n".join(f"Parrafo {index}." for index in range(20))
        chunks = chunk_text(text, max_chars=60, overlap=0)
        assert " ".join(chunks).split() == text.split()


class TestConfigurationIsValidated:
    @pytest.mark.parametrize(
        ("max_chars", "overlap"),
        [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 200)],
    )
    def test_nonsense_settings_fail_loudly(self, max_chars: int, overlap: int) -> None:
        """An overlap at or above the chunk size makes every chunk a copy."""
        with pytest.raises(ValueError):
            chunk_text("hola", max_chars=max_chars, overlap=overlap)
