"""Product tags cleaned once (Feature 018, RF-001)."""

from __future__ import annotations

import pytest

from app.services.product_tags import TagsInvalid, clean_tags, split_tags, tag_key


def test_repeats_differ_only_in_case_accents_or_spaces() -> None:
    assert clean_tags(["Oferta", "oferta ", "Gestión", "gestion", "  dos   palabras "]) == [
        "Oferta",
        "Gestión",
        "dos palabras",
    ]


def test_empty_and_hash_prefixed() -> None:
    assert clean_tags(["", "  ", "#verano"]) == ["verano"]


def test_limits() -> None:
    with pytest.raises(TagsInvalid):
        clean_tags([f"t{i}" for i in range(11)])
    with pytest.raises(TagsInvalid):
        clean_tags(["x" * 31])
    assert len(clean_tags([f"t{i}" for i in range(10)])) == 10


def test_a_cell_splits_on_commas_and_semicolons() -> None:
    assert clean_tags(split_tags("oferta, verano; SIAF")) == ["oferta", "verano", "SIAF"]


def test_the_key() -> None:
    assert tag_key("  Gestión  Pública ") == "gestion publica"
