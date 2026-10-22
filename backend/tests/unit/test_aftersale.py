"""The small pieces of the after-sale module that need no database (feature 019, phase G)."""

from __future__ import annotations

import io
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

from app.aftersale.pdf_writer import SimplePdf
from app.aftersale.returns import ReturnError, pick_lines
from app.api.v1.aftersale import ReturnIn
from app.models.aftersale import RETURN_REASONS, RETURN_TYPES
from app.services.media_library import check


class TestThePdfWriter:
    def test_it_is_a_pdf_that_a_reader_opens_with_the_accents_in_place(self) -> None:
        pdf = SimplePdf()
        pdf.title("Resumen del pedido #12")
        pdf.text("Cliente: Ñandú Pérez — 2 × Polo básico (talla M) \\ barra")

        data = pdf.render()

        assert check(data).tipo == "documento"
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        assert "Resumen del pedido #12" in text
        assert "Ñandú Pérez" in text and "Polo básico" in text

    def test_a_long_document_goes_on_to_the_next_page(self) -> None:
        pdf = SimplePdf()
        for number in range(120):
            pdf.text(f"Línea {number}")

        assert len(PdfReader(io.BytesIO(pdf.render())).pages) >= 3

    def test_a_token_longer_than_the_line_is_cut_instead_of_overflowing(self) -> None:
        pdf = SimplePdf()
        pdf.text("x" * 400)

        assert len(PdfReader(io.BytesIO(pdf.render())).pages) == 1

    def test_a_character_outside_the_encoding_does_not_break_the_document(self) -> None:
        pdf = SimplePdf()
        pdf.text("Gracias 🙌 por tu compra")

        assert "Gracias" in PdfReader(io.BytesIO(pdf.render())).pages[0].extract_text()


def _line(name: str, quantity: int = 1, product: uuid.UUID | None = None) -> Any:
    return SimpleNamespace(
        nombre=name, cantidad=quantity, product_id=product, precio_unitario=Decimal("10")
    )


class TestPickingTheLines:
    def test_no_names_means_the_whole_order(self) -> None:
        assert pick_lines([_line("Polo")], []) == []

    def test_a_name_matches_without_caring_for_accents_or_case(self) -> None:
        picked = pick_lines([_line("Polo básico", 2)], [{"nombre": "POLO BASICO"}])

        assert picked == [{"nombre": "Polo básico", "cantidad": 2}]

    def test_the_quantity_is_cut_to_what_was_bought(self) -> None:
        picked = pick_lines([_line("Polo", 2)], [{"nombre": "polo", "cantidad": 9}])

        assert picked[0]["cantidad"] == 2

    def test_something_that_was_not_bought_is_refused(self) -> None:
        with pytest.raises(ReturnError) as caught:
            pick_lines([_line("Polo")], [{"nombre": "Zapatillas"}])

        assert caught.value.code == "item_not_in_order"
        assert "Polo" in str(caught.value)


def test_the_request_body_allows_exactly_the_types_and_reasons_of_the_database() -> None:
    schema = ReturnIn.model_json_schema()["properties"]

    assert tuple(schema["tipo"]["enum"]) == RETURN_TYPES
    assert tuple(schema["motivo"]["enum"]) == RETURN_REASONS
