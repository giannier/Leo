"""What a quote asks for as the owner writes it: questions, each under a short name.

The data are Bebidas yanayacu's, read from production on 2026-10-26 (product names and questions
only): the owner wrote full questions per product, not the short names the first version assumed.
"""

from __future__ import annotations

import pytest

from app.agent.tools.quote import _match_product
from app.process.quote_data import data_of, gloss, labels_of, topic_of

ALCALINA = [
    "¿Cuántas botellas de Agua Alcalina de 625 ml necesitas?",
    "¿Para qué fecha necesitas tu pedido?",
    "¿A qué distrito o dirección deseas recibirlo?",
    "¿Es para consumo personal, un evento, tu gimnasio o tu negocio?",
    "¿Necesitas una compra puntual o pedidos recurrentes?",
]
MESA_625 = [
    "¿Cuántas botellas de 500 ml necesitas?",
    "¿Para qué fecha necesitas el pedido?",
    "¿A qué distrito o dirección se realizará la entrega?",
    "¿Es para consumo personal, un evento o tu negocio?",
]
BIDON = [
    "¿Cuántas unidades de agua de 20 litros necesitas?",
    "¿Para qué fecha necesitas el pedido?",
    "¿A qué distrito o dirección deseas recibirlo?",
    "¿Es para tu hogar, oficina o negocio?",
    "¿Necesitas una entrega puntual o pedidos recurrentes?",
]
USUAL = ["cantidad", "fecha", "distrito o dirección", "uso", "frecuencia"]


class TestTheOwnersQuestions:
    @pytest.mark.parametrize("entries", [ALCALINA, BIDON])
    def test_each_question_gets_the_name_of_what_it_asks(self, entries: list[str]) -> None:
        assert [d.label for d in data_of(entries)] == USUAL

    def test_a_product_that_does_not_ask_how_often_has_four(self) -> None:
        assert [d.label for d in data_of(MESA_625)] == USUAL[:4]

    def test_the_owner_s_wording_stays_with_the_name(self) -> None:
        by_label = {d.label: d.question for d in data_of(ALCALINA)}

        assert by_label["fecha"] == "¿Para qué fecha necesitas tu pedido?"
        assert "625 ml" in by_label["cantidad"]

    def test_the_products_together_ask_each_thing_once(self) -> None:
        assert labels_of([ALCALINA, MESA_625, BIDON]) == tuple(USUAL)

    @pytest.mark.parametrize(
        ("entry", "label"),
        [
            ("Cantidad por pedido", "cantidad"),
            ("Para cuándo lo necesitas", "fecha"),
            ("Día y hora", "fecha"),
            ("Fechas", "fecha"),
            ("Para cuántas personas", "personas"),
            ("Número de personas", "personas"),
            ("Frecuencia de compra", "frecuencia"),
            ("Presupuesto aproximado", "presupuesto"),
            ("Distrito", "distrito o dirección"),
            ("Zona que te interesa", "distrito o dirección"),
        ],
    )
    def test_the_phrases_the_rubros_suggest_are_recognised(self, entry: str, label: str) -> None:
        assert topic_of(entry) == label

    @pytest.mark.parametrize(
        ("entry", "label"),
        [
            ("Modelo o tipo de vehículo", "modelo o tipo de vehículo"),
            ("¿Qué marca prefieres?", "qué marca prefieres"),
            ("Destino", "destino"),
        ],
    )
    def test_anything_else_keeps_its_own_words_cleaned(self, entry: str, label: str) -> None:
        assert data_of([entry])[0].label == label

    def test_a_long_entry_is_cut_at_a_word(self) -> None:
        label = data_of(["¿Cuál es el nombre completo de la persona que recibirá el pedido?"])[0]

        assert len(label.label) <= 40 and not label.label.endswith(" ")

    def test_two_entries_about_the_same_thing_do_not_lose_one(self) -> None:
        data = data_of(["¿Cuántas botellas?", "¿Cuántas cajas necesitas?"])

        assert [d.label for d in data] == ["cantidad", "cuántas cajas necesitas"]

    def test_empty_entries_are_skipped(self) -> None:
        assert data_of(["", "   ", "Fecha"]) == data_of(["Fecha"])

    def test_the_short_names_the_first_version_assumed_still_work(self) -> None:
        assert [d.label for d in data_of(["cantidad", "fecha", "tipo de local"])] == [
            "cantidad",
            "fecha",
            "tipo de local",
        ]


class TestTheFieldNotes:
    def test_only_the_labels_that_are_not_obvious_have_a_gloss(self) -> None:
        assert gloss("uso") and gloss("frecuencia")
        assert gloss("cantidad") is None and gloss("fecha") is None
        assert gloss("modelo o tipo de vehículo") is None


class TestWhichProductTheCustomerMeans:
    class _Row:
        def __init__(self, name: str) -> None:
            self.name = name

    ROWS = [
        _Row("Agua Alcalina"),
        _Row("Agua de Mesa de 625 ml"),
        _Row("Agua de Mesa de 7 L"),
        _Row("Bidon de Agua de 20L"),
        _Row("Caja de Agua de 20L"),
    ]

    @pytest.mark.parametrize(
        ("said", "name"),
        [
            ("bidón de 20 litros", "Bidon de Agua de 20L"),
            ("la caja de agua de 20 L", "Caja de Agua de 20L"),
            ("agua alcalina", "Agua Alcalina"),
            ("agua de mesa de 7 L", "Agua de Mesa de 7 L"),
        ],
    )
    def test_a_name_that_tells_one_from_the_others_chooses_it(self, said: str, name: str) -> None:
        match = _match_product(said, self.ROWS)  # type: ignore[arg-type]

        assert match is not None and match.name == name

    @pytest.mark.parametrize(
        "said", ["agua", "agua de 20 L", "todas las presentaciones", "para negocio"]
    )
    def test_a_name_that_fits_several_or_none_chooses_nothing(self, said: str) -> None:
        assert _match_product(said, self.ROWS) is None  # type: ignore[arg-type]
