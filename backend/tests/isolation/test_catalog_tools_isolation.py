"""The catalogue and knowledge tools through the real database
(Feature 004, TASK-417).

`buscar_productos` and `ver_producto` read the catalogue of one business on
behalf of a customer. The leak that would matter most is a price or a product of
another business read aloud, so every test here seeds both businesses and asks as
one of them. The rest checks what the envelope promises: featured products first,
only live ones, a search that misses says so, and an id from another tenant is
indistinguishable from no id at all.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.tools.catalog import (
    LISTED_DESCRIPTIONS_CHARS,
    MAX_CATALOG_RESULTS,
    MIN_LISTED_DESCRIPTION,
    buscar_productos,
    consultar_conocimiento,
    ver_producto,
)
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import Product
from app.models.knowledge import KnowledgeChunk
from app.models.tenant import Tenant
from app.providers.llm import MockLLMProvider
from app.repositories.product import ProductRepository
from tests.isolation.conftest import TenantFixture


def _context(db: Session, fixture: TenantFixture) -> ToolContext:
    return ToolContext(session=db, tenant_id=fixture.tenant_id, llm=MockLLMProvider())


def _add(db: Session, fixture: TenantFixture, name: str, **values: Any) -> Product:
    values.setdefault("price", Decimal("10.00"))
    return ProductRepository(db, fixture.tenant_id).add(name=name, **values)


@pytest.fixture
def seeded(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    _add(db, tenant_a, "Paquete A", price=Decimal("500.00"), bundle_size=5, destacado=True)
    _add(db, tenant_b, "Paquete B", price=Decimal("999.00"), bundle_size=5, destacado=True)


def _text(result: ToolResult) -> str:
    return result.render()


# ------------------------------------------------------------------ isolation


class TestOneBusinessNeverReadsAnothersCatalogue:
    async def test_the_search_is_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        result = await buscar_productos(_context(db, tenant_a), {"consulta": ""})

        text = _text(result)
        assert "Paquete A" in text
        assert "Paquete B" not in text
        assert "999.00" not in text
        assert all(p["nombre"] != "Paquete B" for p in result.data["productos"])

    async def test_the_fallback_listing_is_scoped_too(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        result = await buscar_productos(_context(db, tenant_a), {"consulta": "Paquete B"})

        assert result.data["coincidencia"] is False
        assert "Paquete B" not in _text(result).split("Estos son")[1]

    async def test_another_tenants_product_id_looks_like_no_product(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        theirs = ProductRepository(db, tenant_b.tenant_id).search_active("Paquete B", limit=1)[0]

        result = await ver_producto(_context(db, tenant_a), {"producto_id": str(theirs.id)})

        assert result.status == "no_disponible"
        assert "Paquete B" not in _text(result)
        assert "999.00" not in _text(result)

    async def test_the_same_id_works_for_its_owner(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        mine = ProductRepository(db, tenant_a.tenant_id).search_active("Paquete A", limit=1)[0]

        result = await ver_producto(_context(db, tenant_a), {"producto_id": str(mine.id)})

        assert result.status == "hecho"
        assert result.data["nombre"] == "Paquete A"

    async def test_the_knowledge_tool_is_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        llm = MockLLMProvider()
        vector = (await llm.embed(["política"]))[0]
        for fixture, secret in ((tenant_a, "Política de A"), (tenant_b, "Clave interna de B")):
            db.add(
                KnowledgeChunk(
                    tenant_id=fixture.tenant_id,
                    source_id=fixture.source_id,
                    content=secret,
                    embedding=vector,
                )
            )
        db.flush()

        result = await consultar_conocimiento(
            ToolContext(session=db, tenant_id=tenant_a.tenant_id, llm=llm), {"consulta": "política"}
        )

        assert "Política de A" in _text(result)
        assert "Clave interna de B" not in _text(result)


# --------------------------------------------------------------- the search


class TestSearching:
    async def test_the_envelope_carries_prose_and_data(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Curso de Excel", price=Decimal("120.50"), category="Cursos")

        result = await buscar_productos(_context(db, tenant_a), {"consulta": "excel"})

        assert result.status == "hecho"
        assert result.data["coincidencia"] is True
        item = result.data["productos"][0]
        assert item["precio"] == "120.50"
        assert item["categoria"] == "Cursos"
        assert uuid.UUID(item["producto_id"])
        text = _text(result)
        assert text.startswith("estado: hecho\nProductos disponibles:")
        assert "- Curso de Excel (producto_id: " in text
        assert "S/ 120.50" in text
        assert "(categoría: Cursos)" in text

    async def test_featured_products_come_first(self, db: Session, tenant_a: TenantFixture) -> None:
        _add(db, tenant_a, "Aaa común")
        _add(db, tenant_a, "Zzz estrella", destacado=True)

        result = await buscar_productos(_context(db, tenant_a), {"consulta": ""})

        names = [p["nombre"] for p in result.data["productos"]]
        assert names[:2] == ["Zzz estrella", "Aaa común"]
        assert "[destacado]" in _text(result).split("\n")[2]

    async def test_a_package_says_how_many_selections_it_has(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Paquete 5", bundle_size=5)

        assert "[paquete de 5 selecciones]" in _text(
            await buscar_productos(_context(db, tenant_a), {"consulta": "paquete"})
        )

    async def test_a_quoted_product_has_no_fixed_price(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Diseño web", modo_venta="cotizacion")

        result = await buscar_productos(_context(db, tenant_a), {"consulta": "web"})

        assert "[a cotizar: sin precio fijo]" in _text(result)
        assert result.data["productos"][0]["modo_venta"] == "cotizacion"

    async def test_only_packages(self, db: Session, tenant_a: TenantFixture) -> None:
        _add(db, tenant_a, "Suelto")
        _add(db, tenant_a, "Paquete 3", bundle_size=3)

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "", "solo_paquetes": True}
        )

        assert [p["nombre"] for p in result.data["productos"]] == ["Paquete 3"]

    async def test_the_string_true_is_not_the_boolean(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Suelto")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "", "solo_paquetes": "false"}
        )

        assert "Suelto" in [p["nombre"] for p in result.data["productos"]]

    async def test_by_category(self, db: Session, tenant_a: TenantFixture) -> None:
        _add(db, tenant_a, "Polo", category="Ropa")
        _add(db, tenant_a, "Taza", category="Hogar")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "", "categoria": "ropa"}
        )

        assert [p["nombre"] for p in result.data["productos"]] == ["Polo"]

    async def test_percent_and_underscore_are_taken_literally(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Oferta 50% off")
        _add(db, tenant_a, "Oferta 500 off")
        _add(db, tenant_a, "Mi_producto")
        _add(db, tenant_a, "MiXproducto")

        percent = await buscar_productos(_context(db, tenant_a), {"consulta": "50%"})
        underscore = await buscar_productos(_context(db, tenant_a), {"consulta": "mi_p"})

        assert [p["nombre"] for p in percent.data["productos"]] == ["Oferta 50% off"]
        assert [p["nombre"] for p in underscore.data["productos"]] == ["Mi_producto"]

    async def test_inactive_and_deleted_products_are_never_offered(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Visible")
        _add(db, tenant_a, "Apagado", is_active=False)
        removed = _add(db, tenant_a, "Borrado")
        ProductRepository(db, tenant_a.tenant_id).soft_delete(removed.id)

        result = await buscar_productos(_context(db, tenant_a), {"consulta": ""})

        names = [p["nombre"] for p in result.data["productos"]]
        assert "Visible" in names
        assert "Apagado" not in names
        assert "Borrado" not in names

    async def test_a_miss_offers_the_catalogue_and_says_it_missed(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Polo")

        result = await buscar_productos(_context(db, tenant_a), {"consulta": "zapatillas"})

        assert result.status == "hecho"
        assert result.data["coincidencia"] is False
        assert _text(result).startswith(
            "estado: hecho\nNo hay ningún producto que coincida con 'zapatillas'."
        )

    async def test_a_phrase_matched_word_by_word_stays_in_the_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        # Feature 020, T2.7: «pintura pared» names no product whole; its words do.
        _add(db, tenant_a, "Pintura látex blanca 1 galón")
        _add(db, tenant_a, "Martillo")
        _add(db, tenant_b, "Pintura para pared exterior")

        result = await buscar_productos(_context(db, tenant_a), {"consulta": "pintura pared"})

        assert result.data["coincidencia"] is False  # partial: not what the customer named
        assert [p["nombre"] for p in result.data["productos"]] == ["Pintura látex blanca 1 galón"]
        assert "coinciden con alguna de sus palabras" in _text(result)

    async def test_a_phrase_whose_words_are_all_in_a_name_is_a_match(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Crema hidratante de aloe 200 ml")
        _add(db, tenant_b, "Crema de aloe vera")

        result = await buscar_productos(_context(db, tenant_a), {"consulta": "crema aloe"})

        assert result.data["coincidencia"] is True
        assert [p["nombre"] for p in result.data["productos"]] == [
            "Crema hidratante de aloe 200 ml"
        ]

    async def test_a_missed_category_names_the_category(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Polo", category="Ropa")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "", "categoria": "Autos"}
        )

        assert "que coincida con 'Autos'" in _text(result)

    async def test_missed_packages_are_named_as_such(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Suelto")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "", "solo_paquetes": True}
        )

        assert "que coincida con 'paquetes'" in _text(result)

    async def test_an_empty_catalogue_says_so(self, db: Session, tenant_a: TenantFixture) -> None:
        ProductRepository(db, tenant_a.tenant_id).soft_delete(tenant_a.product_id)

        result = await buscar_productos(_context(db, tenant_a), {"consulta": "polo"})

        assert result.status == "no_disponible"
        assert "El catálogo está vacío" in _text(result)

    async def test_a_listing_is_capped_and_descriptions_are_short(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        for index in range(MAX_CATALOG_RESULTS + 3):
            _add(db, tenant_a, f"Producto {index:02d}", description="x" * 500)

        result = await buscar_productos(_context(db, tenant_a), {"consulta": ""})

        assert len(result.data["productos"]) == MAX_CATALOG_RESULTS
        described = [p["descripcion"] for p in result.data["productos"] if p["descripcion"]]
        assert described
        # The descriptions of a full listing share the space, each cut cleanly with «…».
        share = max(MIN_LISTED_DESCRIPTION, LISTED_DESCRIPTIONS_CHARS // MAX_CATALOG_RESULTS)
        assert all(len(text) <= share and text.endswith("…") for text in described)

    async def test_stock_is_stated_only_when_tracked(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Con stock", stock=7)
        _add(db, tenant_a, "Sin conteo")

        text = _text(await buscar_productos(_context(db, tenant_a), {"consulta": ""}))

        assert "[stock: 7]" in text
        assert text.count("[stock:") == 1


# ----------------------------------------------------------------- one product


class TestViewingAProduct:
    async def test_it_returns_the_full_description(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        product = _add(db, tenant_a, "Curso", description="d" * 500, category="Cursos")

        result = await ver_producto(_context(db, tenant_a), {"producto_id": str(product.id)})

        assert result.status == "hecho"
        assert len(result.data["descripcion"]) == 500
        assert _text(result).startswith("estado: hecho\nProducto:\n- Curso (producto_id: ")

    @pytest.mark.parametrize("bad", ["", "no-es-un-uuid", "123"])
    async def test_an_invalid_id_is_an_error_the_model_can_read(
        self, db: Session, tenant_a: TenantFixture, bad: str
    ) -> None:
        result = await ver_producto(_context(db, tenant_a), {"producto_id": bad})

        assert result.status == "error"
        assert _text(result).startswith("ERROR: producto_id no es válido")

    async def test_an_unknown_id(self, db: Session, tenant_a: TenantFixture) -> None:
        result = await ver_producto(_context(db, tenant_a), {"producto_id": str(uuid.uuid4())})

        assert result.status == "no_disponible"
        assert "buscar_productos" in _text(result)

    async def test_a_switched_off_product_is_not_viewable(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        product = _add(db, tenant_a, "Apagado", is_active=False)

        result = await ver_producto(_context(db, tenant_a), {"producto_id": str(product.id)})

        assert result.status == "no_disponible"

    async def test_a_deleted_product_is_not_viewable(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        product = _add(db, tenant_a, "Borrado")
        ProductRepository(db, tenant_a.tenant_id).soft_delete(product.id)

        result = await ver_producto(_context(db, tenant_a), {"producto_id": str(product.id)})

        assert result.status == "no_disponible"


# ------------------------------------------------------------------- knowledge


class TestKnowledge:
    async def test_an_empty_question_is_an_error(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        result = await consultar_conocimiento(_context(db, tenant_a), {"consulta": "  "})

        assert result.status == "error"
        assert _text(result) == "ERROR: No se indicó qué buscar."

    async def test_nothing_found_says_so_and_forbids_inventing(self, db: Session) -> None:
        # A business with no documents at all (the fixtures' tenants each have one).
        empty = Tenant(name="Sin documentos")
        db.add(empty)
        db.flush()

        result = await consultar_conocimiento(
            ToolContext(session=db, tenant_id=empty.id, llm=MockLLMProvider()),
            {"consulta": "algo raro"},
        )

        assert result.status == "no_disponible"
        assert "No hay información" in _text(result)
        assert "No inventes" in _text(result)

    async def test_what_is_found_is_quoted_and_counted(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        llm = MockLLMProvider()
        vector = (await llm.embed(["horario"]))[0]
        db.add(
            KnowledgeChunk(
                tenant_id=tenant_a.tenant_id,
                source_id=tenant_a.source_id,
                content="Abrimos de 9 a 6.",
                embedding=vector,
            )
        )
        db.flush()

        result = await consultar_conocimiento(
            ToolContext(session=db, tenant_id=tenant_a.tenant_id, llm=llm), {"consulta": "horario"}
        )

        assert result.status == "hecho"
        assert result.data["fragmentos"] >= 1
        assert "Abrimos de 9 a 6." in _text(result)


class TestACategoryTheModelGuessedDoesNotCancelTheName:
    """A business with no categories loaded: "hosting" as a name AND as a category matched
    nothing, so the customer was shown "no match" and the CRM never heard of the lead."""

    async def test_the_name_still_matches_when_the_category_does_not(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Hosting web - Básico")
        _add(db, tenant_a, "Página web profesional")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "hosting", "categoria": "hosting"}
        )

        assert result.data["coincidencia"] is True
        assert [p["nombre"] for p in result.data["productos"]] == ["Hosting web - Básico"]

    async def test_a_category_word_alone_is_tried_against_the_names(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Hosting web - Básico")

        result = await buscar_productos(_context(db, tenant_a), {"categoria": "hosting"})

        assert result.data["coincidencia"] is True

    async def test_a_real_category_still_narrows(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _add(db, tenant_a, "Hosting web - Básico", category="Hosting")
        _add(db, tenant_a, "Hosting avanzado", category="Servidores")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "hosting", "categoria": "Servidores"}
        )

        assert [p["nombre"] for p in result.data["productos"]] == ["Hosting avanzado"]
