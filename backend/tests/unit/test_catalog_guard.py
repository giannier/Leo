"""The catalogue-name detector, both directions.

This is the production detector - `AgentCore` runs it over every reply before
it is sent, and the golden set runs the same code over a model's answer. It
has two jobs and they pull against each other: catch a product name the
business does not sell, and stay silent on prose that merely talks about one.

**The second job is the harder one and the more important.** A guard that
fires on a correct answer turns it into an apology, and the first thing anyone
does with a guard that cries wolf is switch it off - at which point the bug it
was written for comes back with nobody watching. So the prose table below is
longer than the rename table, and a failure in it is the more serious failure.

The renames are pinned as a table because the bug they encode was found by
measurement. The previous detector matched a whitelist of adjective endings:

    CATCH | Inca Kola helada          (-ada)
    CATCH | Inca Kola pausterizada    (-ada)
    MISS  | Inca Kola fresca
    MISS  | Inca Kola entera
    MISS  | Inca Kola light

Those three misses are the reported bug wearing a different suffix, which is
why the whitelist was replaced rather than extended.
"""

from __future__ import annotations

import pytest

from app.agent.catalog_guard import build_catalogue_guard

CATALOGUE = ("Lomo saltado", "Ceviche mixto", "Inca Kola 500ml")

GUARD = build_catalogue_guard(CATALOGUE)

# What the demo tenant is known to have written. Supplied only where the
# caller genuinely has it, which at runtime it does not - see
# `TestWithoutAVocabulary`.
VOCABULARY = (
    "Lomo saltado con papas fritas y arroz",
    "Ceviche mixto pescado y mariscos frescos del dia",
    "Inca Kola 500ml bebidas",
    "Atendemos de lunes a sabado. Hacemos delivery dentro de Miraflores.",
    "Leo Restaurante Demo",
)
GUARD_WITH_VOCABULARY = build_catalogue_guard(CATALOGUE, vocabulary=VOCABULARY)


class TestNamesTheCatalogueDoesNotHave:
    """A descriptor bolted onto a real name is a product that does not exist."""

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            # The five measured shapes. The first two were already caught; the
            # last three are the gap the ending-whitelist left open.
            ("Si, tenemos Inca Kola helada a S/ 5.50.", "inca kola helada"),
            ("Si, tenemos Inca Kola pausterizada a S/ 5.50.", "inca kola pausterizada"),
            ("Si, tenemos Inca Kola fresca a S/ 5.50.", "inca kola fresca"),
            ("Si, tenemos Inca Kola entera a S/ 5.50.", "inca kola entera"),
            ("Si, tenemos Inca Kola light a S/ 5.50.", "inca kola light"),
            # The same five in lower case. A model that does not capitalise is
            # not a model that is telling the truth.
            ("si, tenemos inca kola helada a S/ 5.50.", "inca kola helada"),
            ("si, tenemos inca kola pausterizada a S/ 5.50.", "inca kola pausterizada"),
            ("si, tenemos inca kola fresca a S/ 5.50.", "inca kola fresca"),
            ("si, tenemos inca kola entera a S/ 5.50.", "inca kola entera"),
            ("si, tenemos inca kola light a S/ 5.50.", "inca kola light"),
            # Accents are not a disguise either.
            ("Tenemos Inca Kola fría a S/ 5.50.", "inca kola fria"),
            # The other two products, so the check is not one product's quirk.
            ("El Ceviche mixto especial sale S/ 38.50.", "ceviche mixto especial"),
            ("El Lomo saltado criollo cuesta S/ 32.00.", "lomo saltado criollo"),
            ("Te recomiendo el Ceviche mixto familiar.", "ceviche mixto familiar"),
        ],
    )
    def test_a_bolted_on_descriptor_is_reported(self, answer: str, expected: str) -> None:
        assert GUARD.invented_names(answer) == [expected]


class TestOrdinaryProse:
    """Sentences a correct agent writes. None of them may fire.

    Every case that the eval-check suite already protected is here, plus the
    shape the stop-list exists for: a catalogue name with a verb or a
    preposition immediately after it. "El Lomo saltado viene con papas" is a
    description; "El Lomo saltado criollo" is a rename. One word tells them
    apart and this table is what proves the right one is used.
    """

    @pytest.mark.parametrize(
        "answer",
        [
            # --- already protected by tests/unit/test_eval_checks.py --------
            "Te recomiendo el Lomo saltado, muy rico.",
            "Te recomiendo el Ceviche mixto, muy rico.",
            "Te recomiendo el Inca Kola 500ml, muy rico.",
            "La Inca Kola esta S/ 5.50, es la mas pedida.",
            "El Lomo saltado viene con papas fritas y arroz, cuesta S/ 32.00.",
            "¡Hola! ¿En que te puedo ayudar hoy?",
            "Atendemos de lunes a sabado de 12:00 a 22:00. Los domingos cerramos.",
            "Hacemos delivery solo dentro de Miraflores, cuesta S/ 6.00 y demora 40 minutos.",
            "Lamento mucho lo ocurrido. Te comunico ahora con una persona del equipo.",
            "No manejamos ese producto, disculpa. ¿Te muestro lo que si tenemos?",
            "Soy Leo, el asistente del Restaurante Demo. Dime que se te antoja.",
            "Tenemos Lomo saltado, Ceviche mixto y Inca Kola 500ml.",
            # --- a verb follows the catalogue name --------------------------
            "El Ceviche mixto cuesta S/ 38.50.",
            "La Inca Kola está fría, recién salida de la refrigeradora.",
            "El Lomo saltado incluye papas fritas y arroz blanco.",
            "El Ceviche mixto lleva pescado y mariscos frescos del día.",
            "La Inca Kola 500ml sale S/ 5.50 en el local.",
            "El Lomo saltado queda listo en unos 20 minutos.",
            "El Ceviche mixto tiene pescado del día y no lleva ají.",
            "El Lomo saltado es nuestro plato más pedido.",
            "El Ceviche mixto vale S/ 38.50 y alcanza para dos.",
            "Las Inca Kola 500ml vienen bien heladas.",
            # --- a preposition or a conjunction follows it ------------------
            "El Lomo saltado para llevar demora unos 20 minutos.",
            "El Ceviche mixto con doble porción de pescado no lo manejamos.",
            "La Inca Kola sin azúcar no la tenemos, disculpa.",
            "El Lomo saltado y el Ceviche mixto salen S/ 70.50 juntos.",
            "El Ceviche mixto desde las 12:00, cuando llega el pescado.",
            "La Inca Kola por unidad o por caja, como prefieras.",
            # --- an adverb or a determiner follows it -----------------------
            "El Ceviche mixto también se puede pedir sin ají.",
            "El Lomo saltado solo lo preparamos hasta las 5 de la tarde.",
            "La Inca Kola siempre está fría, no te preocupes.",
            "El Ceviche mixto ahora está disponible, acaba de llegar el pescado.",
            "El Lomo saltado todavía alcanza para hoy.",
            "El Ceviche mixto nuestro se prepara al momento.",
        ],
    )
    def test_prose_about_a_product_is_not_a_rename(self, answer: str) -> None:
        assert GUARD.invented_names(answer) == []

    def test_a_shortened_name_is_somebody_being_brief_not_inventing(self) -> None:
        """ "Inca Kola" for "Inca Kola 500ml" is a person talking normally."""
        assert GUARD.invented_names("La Inca Kola es la mas pedida.") == []

    @pytest.mark.parametrize("name", CATALOGUE)
    def test_every_catalogue_name_is_sayable(self, name: str) -> None:
        """A guard that fires on the catalogue would fire on every reply."""
        assert GUARD.invented_names(f"Te recomiendo el {name}, muy rico.") == []


class TestNamesIntroducedFromNowhere:
    """The other half, and only where a vocabulary can be supplied."""

    def test_a_capitalised_name_the_tenant_never_wrote_is_reported(self) -> None:
        answer = "Claro, tenemos Sushi variado a S/ 20.00."
        assert GUARD_WITH_VOCABULARY.invented_names(answer) == ["sushi variado"]

    @pytest.mark.parametrize(
        "answer",
        [
            "Soy Leo, el asistente del Restaurante Demo.",
            "Hacemos delivery dentro de Miraflores nada mas.",
            "Tenemos Lomo saltado, Ceviche mixto y Inca Kola 500ml.",
            "Atendemos de lunes a sabado. Los domingos cerramos.",
        ],
    )
    def test_words_the_tenant_did_write_are_left_alone(self, answer: str) -> None:
        assert GUARD_WITH_VOCABULARY.invented_names(answer) == []


class TestWithoutAVocabulary:
    """What the runtime guard can and cannot do.

    `AgentCore` knows the tenant's catalogue and nothing else - not the
    uploaded documents, not the business's own proper nouns. Judging
    capitalised words against that partial picture would flag "Miraflores",
    "Yape" and the customer's own name on answers that are entirely correct,
    so the check stands down instead. The anchored half still runs, and the
    anchored half is the reported bug.
    """

    @pytest.mark.parametrize(
        "answer",
        [
            "Claro, tenemos Sushi variado a S/ 20.00.",
            "Te esperamos en Miraflores, cerca del Parque Kennedy.",
            "Puedes pagar con Yape o con Plin, como prefieras.",
        ],
    )
    def test_a_capitalised_word_is_not_judged(self, answer: str) -> None:
        assert GUARD.invented_names(answer) == []

    def test_the_anchored_half_still_runs(self) -> None:
        assert GUARD.invented_names("Tenemos Inca Kola fresca.") == ["inca kola fresca"]


class TestOverlappingCatalogueNames:
    """A catalogue where one name is a prefix of another.

    Real catalogues do this constantly - "Café", "Café con leche", "Café con
    leche grande". The longest match has to win whichever order the rows come
    back in, or the extra words of the longer name read as a rename of the
    shorter one and every mention of the real product becomes an alarm.
    """

    CATALOGUE = ("Cafe con leche grande", "Cafe", "Cafe con leche")

    def setup_method(self) -> None:
        self.guard = build_catalogue_guard(self.CATALOGUE)

    @pytest.mark.parametrize("name", CATALOGUE)
    def test_every_overlapping_name_is_sayable(self, name: str) -> None:
        assert self.guard.invented_names(f"Tenemos {name} recien hecho.") == []

    def test_a_descriptor_past_the_longest_name_is_still_caught(self) -> None:
        assert self.guard.invented_names("Tenemos Cafe con leche grande helado.") == [
            "cafe con leche grande helado"
        ]

    def test_a_name_that_is_only_a_prefix_still_anchors(self) -> None:
        assert self.guard.invented_names("Tenemos Cafe americano.") == ["cafe americano"]


class TestAGuardWithNothingToJudge:
    def test_an_empty_catalogue_reports_nothing(self) -> None:
        """An empty catalogue means the answer cannot be judged, not that
        every word in it was invented. A tenant mid-onboarding would otherwise
        have every reply replaced by an apology."""
        empty = build_catalogue_guard(())
        assert empty.invented_names("Tenemos Inca Kola fresca a S/ 5.50.") == []

    def test_blank_names_are_discarded_rather_than_matching_everything(self) -> None:
        guard = build_catalogue_guard(("", "   ", "Lomo saltado"))
        assert guard.catalogue == ("Lomo saltado",)
        assert guard.invented_names("¡Hola! ¿En que te ayudo?") == []
