"""`enviar_recurso`, `mostrar_opciones`, `elegir_camino` and the catalogue
extension, through the real database (Feature 004, TASK-420).

Two businesses use the same resource keys and the same vocabulary, so the
tenant filter is what keeps one business's flyer, products and promotions out of
the other's customer chat: every test seeds both and asks as one. The rest pins
the contract of `contracts/api.md` section 7: a file is never sent twice
(`omitido`), WhatsApp's limits are met by truncating (3 buttons of 20, 10 rows
of 24 and 72, a body of 1024), the path enum is per tenant, and a spent turn
budget sends nothing (`no_disponible`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.budget import TurnBudget
from app.agent.outbound import OutboundFailed
from app.agent.tools.catalog import buscar_productos, ver_producto
from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.registry import ToolContext, ToolResult
from app.agent.tools.sales import elegir_camino, enviar_recurso, mostrar_opciones
from app.models.business import Product
from app.models.media import MediaAsset
from app.models.sales_config import Promotion
from app.process.facts import Facts
from app.process.templates import TEMPLATES
from app.process.templates.base import EffectiveOptions, Stage
from app.providers.llm import MockLLMProvider
from app.providers.whatsapp.base import Button, ListSection
from app.repositories.business import BusinessProfileRepository
from app.repositories.media import MediaAssetRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import PromotionRepository
from app.schemas.process_options import ProcessOptions
from tests.isolation.conftest import TenantFixture

SHOP = TEMPLATES["productos"]
CERT = TEMPLATES["certificaciones"]


class RecordingOutbound:
    """A turn's outbound side that remembers what it was asked to send."""

    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing
        self.media: list[MediaAsset] = []
        self.buttons: list[tuple[str, list[Button]]] = []
        self.lists: list[tuple[str, str, list[ListSection]]] = []

    @property
    def sent(self) -> int:
        return len(self.media) + len(self.buttons) + len(self.lists)

    async def send_media(self, asset: MediaAsset) -> None:
        if self.failing:
            raise OutboundFailed("down")
        self.media.append(asset)

    async def send_buttons(self, body: str, buttons: Sequence[Button]) -> None:
        if self.failing:
            raise OutboundFailed("down")
        self.buttons.append((body, list(buttons)))

    async def send_list(self, body: str, button_text: str, sections: Sequence[ListSection]) -> None:
        if self.failing:
            raise OutboundFailed("down")
        self.lists.append((body, button_text, list(sections)))


def _options(*paths: tuple[str, str, str]) -> EffectiveOptions:
    if not paths:
        return SHOP.resolve()
    return SHOP.resolve(
        ProcessOptions.model_validate(
            {
                "calificacion": {
                    "activa": True,
                    "pregunta": "¿Qué buscas?",
                    "caminos": [
                        {"clave": key, "titulo": title, "tipo": kind} for key, title, kind in paths
                    ],
                }
            }
        )
    )


def _context(
    db: Session,
    fixture: TenantFixture,
    *,
    outbound: RecordingOutbound | None = None,
    facts: Facts | None = None,
    options: EffectiveOptions | None = None,
    budget: TurnBudget | None = None,
) -> ToolContext:
    return ToolContext(
        session=db,
        tenant_id=fixture.tenant_id,
        llm=MockLLMProvider(),
        adapter=outbound,
        facts=facts or Facts(),
        options=options or SHOP.resolve(),
        budget=budget or TurnBudget(),
    )


def _asset(
    db: Session, fixture: TenantFixture, clave: str = "flyer", **overrides: Any
) -> MediaAsset:
    values: dict[str, Any] = {
        "clave": clave,
        "tipo": "imagen",
        "titulo": f"Titulo {clave}",
        "cuando_usar": "Cuando pidan el flyer",
        "storage_key": f"media/{uuid.uuid4()}.png",
        "filename": f"{clave}.png",
        "mime_type": "image/png",
        "size_bytes": 10,
    }
    values.update(overrides)
    return MediaAssetRepository(db, fixture.tenant_id).add(**values)


def _product(db: Session, fixture: TenantFixture, name: str, **values: Any) -> Product:
    values.setdefault("price", Decimal("10.00"))
    return ProductRepository(db, fixture.tenant_id).add(name=name, **values)


def _promotion(db: Session, fixture: TenantFixture, nombre: str, **values: Any) -> Promotion:
    values.setdefault("uso", "automatica")
    values.setdefault("tipo", "porcentaje")
    values.setdefault("valor", Decimal("20"))
    return PromotionRepository(db, fixture.tenant_id).add(nombre=nombre, **values)


# ------------------------------------------------------------- enviar_recurso


class TestSendingAResource:
    async def test_it_sends_the_file_and_remembers_it(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        asset = _asset(db, tenant_a)
        outbound = RecordingOutbound()
        context = _context(db, tenant_a, outbound=outbound)

        result = await enviar_recurso(context, {"clave": "flyer"})

        assert result.status == "hecho"
        assert outbound.media == [asset]
        assert context.facts_patch["recursos_enviados"] == ["flyer"]
        assert context.budget.spent == 1

    async def test_a_file_already_sent_is_not_sent_again(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a)
        outbound = RecordingOutbound()
        context = _context(
            db, tenant_a, outbound=outbound, facts=Facts(recursos_enviados=["flyer"])
        )

        result = await enviar_recurso(context, {"clave": "flyer"})

        assert result.status == "omitido"
        assert outbound.sent == 0
        assert context.budget.spent == 0

    async def test_calling_it_twice_in_one_turn_sends_once(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a)
        outbound = RecordingOutbound()
        context = _context(db, tenant_a, outbound=outbound)

        first = await enviar_recurso(context, {"clave": "flyer"})
        second = await enviar_recurso(context, {"clave": "flyer"})

        assert (first.status, second.status) == ("hecho", "omitido")
        assert len(outbound.media) == 1

    async def test_an_unknown_key_is_not_available(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        outbound = RecordingOutbound()

        result = await enviar_recurso(_context(db, tenant_a, outbound=outbound), {"clave": "nada"})

        assert result.status == "no_disponible"
        assert outbound.sent == 0

    async def test_an_inactive_resource_is_not_available(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a, is_active=False)

        result = await enviar_recurso(
            _context(db, tenant_a, outbound=RecordingOutbound()), {"clave": "flyer"}
        )

        assert result.status == "no_disponible"

    async def test_another_businesss_file_is_never_sent(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _asset(db, tenant_b, "secreto")
        outbound = RecordingOutbound()

        result = await enviar_recurso(
            _context(db, tenant_a, outbound=outbound), {"clave": "secreto"}
        )

        assert result.status == "no_disponible"
        assert outbound.sent == 0

    async def test_the_same_key_sends_each_businesss_own_file(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = _asset(db, tenant_a, "saludo", titulo="Saludo A")
        _asset(db, tenant_b, "saludo", titulo="Saludo B")
        outbound = RecordingOutbound()

        await enviar_recurso(_context(db, tenant_a, outbound=outbound), {"clave": "saludo"})

        assert outbound.media == [mine]

    async def test_a_spent_budget_sends_nothing(self, db: Session, tenant_a: TenantFixture) -> None:
        _asset(db, tenant_a)
        _asset(db, tenant_a, "otro")
        outbound = RecordingOutbound()
        context = _context(db, tenant_a, outbound=outbound)
        # 3 messages, 1 kept for the text: two sends, then the budget is spent.
        await enviar_recurso(context, {"clave": "flyer"})
        await enviar_recurso(context, {"clave": "otro"})
        _asset(db, tenant_a, "tercero")

        result = await enviar_recurso(context, {"clave": "tercero"})

        assert result.status == "no_disponible"
        assert "próximo mensaje" in (result.decir or "")
        assert outbound.sent == 2
        assert "tercero" not in context.facts_patch["recursos_enviados"]

    async def test_without_an_adapter_nothing_can_be_sent(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a)

        result = await enviar_recurso(_context(db, tenant_a), {"clave": "flyer"})

        assert result.status == "no_disponible"

    async def test_a_failed_send_is_an_error_and_is_not_remembered(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a)
        context = _context(db, tenant_a, outbound=RecordingOutbound(failing=True))

        result = await enviar_recurso(context, {"clave": "flyer"})

        assert result.status == "error"
        assert "recursos_enviados" not in context.facts_patch
        assert context.budget.spent == 0


# ----------------------------------------------------------- mostrar_opciones


def _text(result: ToolResult) -> str:
    return result.render()


class TestShowingOptions:
    async def test_up_to_three_options_go_as_buttons(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        outbound = RecordingOutbound()
        context = _context(
            db,
            tenant_a,
            outbound=outbound,
            options=_options(
                ("comprar", "Quiero comprar", "venta"), ("asesor", "Hablar", "asesoria")
            ),
        )

        result = await mostrar_opciones(context, {"tipo": "caminos", "texto": "¿Qué buscas?"})

        assert result.status == "hecho"
        body, buttons = outbound.buttons[0]
        assert body == "¿Qué buscas?"
        assert buttons == [
            Button("camino:comprar", "Quiero comprar"),
            Button("camino:asesor", "Hablar"),
        ]
        assert context.facts_patch["opciones_mostradas"] == ["caminos"]
        assert context.budget.spent == 1

    async def test_button_titles_are_cut_to_twenty_characters(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _product(db, tenant_a, "Paquete de certificaciones de gestión pública", bundle_size=5)
        outbound = RecordingOutbound()

        await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound),
            {"tipo": "productos", "texto": "Paquetes", "filtro": "paquetes"},
        )

        title = outbound.buttons[0][1][0].title
        assert len(title) == 20
        assert title.endswith("…")

    async def test_the_body_is_cut_to_1024_characters(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        outbound = RecordingOutbound()
        context = _context(
            db,
            tenant_a,
            outbound=outbound,
            options=_options(("aa", "A", "venta"), ("bb", "B", "asesoria")),
        )

        await mostrar_opciones(context, {"tipo": "caminos", "texto": "x" * 1500})

        assert len(outbound.buttons[0][0]) == 1024

    async def test_more_than_three_options_go_as_a_list_of_at_most_ten_rows(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        for number in range(12):
            _product(
                db,
                tenant_a,
                f"Producto con un nombre muy largo número {number:02d}",
                description="d",
            )
        outbound = RecordingOutbound()
        context = _context(db, tenant_a, outbound=outbound)

        result = await mostrar_opciones(context, {"tipo": "productos", "texto": "Mira"})

        assert result.status == "hecho"
        assert result.data["formato"] == "lista"
        assert (result.data["mostradas"], result.data["total"]) == (10, 10)
        body, button_text, sections = outbound.lists[0]
        rows = sections[0].rows
        assert len(rows) == 10
        assert button_text == "Ver opciones"
        assert all(len(row.title) <= 24 for row in rows)
        assert all(row.id.startswith("producto:") for row in rows)
        assert all(row.description == "S/ 10.00" for row in rows)

    async def test_row_descriptions_are_cut_to_seventy_two_characters(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        for name in ("Uno", "Dos", "Tres", "Cuatro"):
            _promotion(db, tenant_a, name, descripcion="d" * 200)
        outbound = RecordingOutbound()

        await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound), {"tipo": "promociones", "texto": "Ofertas"}
        )

        rows = outbound.lists[0][2][0].rows
        assert len(rows) == 4
        assert all(len(row.description or "") == 72 for row in rows)
        assert all(row.id.startswith("promo:") for row in rows)

    async def test_products_of_another_business_are_never_listed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _product(db, tenant_b, "Secreto de B", price=Decimal("999.00"))
        outbound = RecordingOutbound()

        await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound), {"tipo": "productos", "texto": "Mira"}
        )

        titles = [button.title for _, buttons in outbound.buttons for button in buttons]
        assert titles == ["Product A"]

    async def test_featured_products_come_first(self, db: Session, tenant_a: TenantFixture) -> None:
        _product(db, tenant_a, "Zeta destacado", destacado=True)
        outbound = RecordingOutbound()

        await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound), {"tipo": "productos", "texto": "Mira"}
        )

        assert outbound.buttons[0][1][0].title == "Zeta destacado"

    async def test_the_bundles_filter_keeps_only_packages(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _product(db, tenant_a, "Paquete 5", bundle_size=5)
        outbound = RecordingOutbound()

        result = await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound),
            {"tipo": "productos", "texto": "Paquetes", "filtro": "Paquetes"},
        )

        assert [button.title for button in outbound.buttons[0][1]] == ["Paquete 5"]
        assert result.data["total"] == 1

    async def test_any_other_filter_is_a_category(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _product(db, tenant_a, "Curso SIAF", category="Gestión pública")
        _product(db, tenant_a, "Curso Excel", category="Ofimática")
        outbound = RecordingOutbound()
        context = _context(db, tenant_a, outbound=outbound)

        await mostrar_opciones(
            context, {"tipo": "productos", "texto": "Cursos", "filtro": "gestión"}
        )

        assert [button.title for button in outbound.buttons[0][1]] == ["Curso SIAF"]
        assert context.facts_patch["opciones_mostradas"] == ["productos:gestión"]

    async def test_categories_are_the_distinct_ones_of_live_products(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _product(db, tenant_a, "Curso SIAF", category="Gestión pública")
        _product(db, tenant_a, "Curso SIAF 2", category="Gestión pública")
        _product(db, tenant_a, "Curso Excel", category="Ofimática")
        _product(db, tenant_a, "Apagado", category="Oculta", is_active=False)
        _product(db, tenant_b, "Ajeno", category="De otro negocio")
        outbound = RecordingOutbound()

        await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound), {"tipo": "categorias", "texto": "Áreas"}
        )

        buttons = outbound.buttons[0][1]
        assert [(b.id, b.title) for b in buttons] == [
            ("categoria:gestion-publica", "Gestión pública"),
            ("categoria:ofimatica", "Ofimática"),
        ]

    async def test_only_automatic_promotions_are_listed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _promotion(db, tenant_a, "Descuento de lanzamiento", descripcion="20 % en todo")
        _promotion(db, tenant_a, "Cupón de cierre", uso="cierre", vigencia_horas=24)
        _promotion(db, tenant_a, "Ya terminó", termina_en=datetime.now(UTC) - timedelta(days=1))
        _promotion(db, tenant_b, "Promo de B")
        outbound = RecordingOutbound()

        await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound), {"tipo": "promociones", "texto": "Ofertas"}
        )

        titles = [button.title for button in outbound.buttons[0][1]]
        assert titles == ["Descuento de lanzamiento"[:19] + "…"]

    async def test_showing_products_marks_that_the_customer_saw_them(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(db, tenant_a, outbound=RecordingOutbound())

        await mostrar_opciones(context, {"tipo": "productos", "texto": "Mira"})

        assert context.facts_patch["vio_productos"] is True
        assert context.facts_patch["opciones_mostradas"] == ["productos"]

    async def test_other_option_types_do_not_mark_products_as_seen(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _product(db, tenant_a, "Curso", category="Ofimática")
        context = _context(db, tenant_a, outbound=RecordingOutbound())

        await mostrar_opciones(context, {"tipo": "categorias", "texto": "Áreas"})

        assert "vio_productos" not in context.facts_patch

    async def test_the_same_options_are_recorded_once(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(db, tenant_a, outbound=RecordingOutbound())

        await mostrar_opciones(context, {"tipo": "productos", "texto": "Mira"})
        await mostrar_opciones(context, {"tipo": "productos", "texto": "De nuevo"})

        assert context.facts_patch["opciones_mostradas"] == ["productos"]

    async def test_nothing_to_show_is_not_available_and_sends_nothing(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        outbound = RecordingOutbound()

        result = await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound), {"tipo": "promociones", "texto": "Ofertas"}
        )

        assert result.status == "no_disponible"
        assert outbound.sent == 0

    async def test_a_spent_budget_sends_nothing(self, db: Session, tenant_a: TenantFixture) -> None:
        outbound = RecordingOutbound()
        budget = TurnBudget()
        budget.consume()
        budget.consume()
        context = _context(db, tenant_a, outbound=outbound, budget=budget)

        result = await mostrar_opciones(context, {"tipo": "productos", "texto": "Mira"})

        assert result.status == "no_disponible"
        assert "próximo mensaje" in (result.decir or "")
        assert outbound.sent == 0
        assert "opciones_mostradas" not in context.facts_patch

    async def test_a_failed_send_is_an_error_and_is_not_remembered(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(db, tenant_a, outbound=RecordingOutbound(failing=True))

        result = await mostrar_opciones(context, {"tipo": "productos", "texto": "Mira"})

        assert result.status == "error"
        assert context.facts_patch == {}
        assert context.budget.spent == 0

    async def test_an_invalid_type_or_empty_text_is_an_error(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(db, tenant_a, outbound=RecordingOutbound())

        assert (await mostrar_opciones(context, {"tipo": "otro", "texto": "x"})).status == "error"
        assert (
            await mostrar_opciones(context, {"tipo": "productos", "texto": "  "})
        ).status == "error"

    async def test_without_an_adapter_nothing_can_be_sent(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        result = await mostrar_opciones(
            _context(db, tenant_a), {"tipo": "productos", "texto": "Mira"}
        )

        assert result.status == "no_disponible"

    async def test_reaching_it_through_the_stage_registry_gives_it_the_tenants_paths(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        options = _options(("comprar", "Comprar", "venta"), ("asesor", "Hablar", "asesoria"))
        registry = registry_for_stage(SHOP, Stage.CALIFICAR, Facts(), options)
        outbound = RecordingOutbound()
        # The context deliberately carries no options: the registry supplies them.
        context = ToolContext(
            session=db, tenant_id=tenant_a.tenant_id, llm=MockLLMProvider(), adapter=outbound
        )
        tool = registry.get("mostrar_opciones")
        assert tool is not None

        await tool.handler(context, {"tipo": "caminos", "texto": "¿Qué buscas?"})

        assert [b.id for b in outbound.buttons[0][1]] == ["camino:comprar", "camino:asesor"]


# -------------------------------------------------------------- elegir_camino


class TestChoosingAPath:
    async def test_it_records_the_path(self, db: Session, tenant_a: TenantFixture) -> None:
        context = _context(
            db,
            tenant_a,
            options=_options(("comprar", "Comprar", "venta"), ("asesor", "Hablar", "asesoria")),
        )

        result = await elegir_camino(context, {"camino": "asesor"})

        assert result.status == "hecho"
        assert context.facts_patch == {"camino": "asesor"}
        assert result.data == {"camino": "asesor", "tipo": "asesoria"}

    async def test_the_path_already_chosen_is_skipped(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(
            db,
            tenant_a,
            facts=Facts(camino="comprar"),
            options=_options(("comprar", "Comprar", "venta"), ("asesor", "Hablar", "asesoria")),
        )

        result = await elegir_camino(context, {"camino": "comprar"})

        assert result.status == "omitido"
        assert context.facts_patch == {}

    async def test_a_path_the_tenant_never_configured_is_an_error(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(
            db,
            tenant_a,
            options=_options(("comprar", "Comprar", "venta"), ("asesor", "Hablar", "asesoria")),
        )

        result = await elegir_camino(context, {"camino": "certificacion"})

        assert result.status == "error"
        assert context.facts_patch == {}

    async def test_without_paths_there_is_nothing_to_choose(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        result = await elegir_camino(_context(db, tenant_a), {"camino": "comprar"})

        assert result.status == "error"

    async def test_the_enum_is_each_tenants_own(self) -> None:
        mine = registry_for_stage(
            SHOP, Stage.CALIFICAR, Facts(), _options(("aa", "A", "venta"), ("bb", "B", "asesoria"))
        )
        theirs = registry_for_stage(
            SHOP, Stage.CALIFICAR, Facts(), _options(("xx", "X", "venta"), ("yy", "Y", "asesoria"))
        )

        def enum(registry: Any) -> list[str]:
            tool = registry.get("elegir_camino")
            assert tool is not None
            keys: list[str] = tool.spec.parameters["properties"]["camino"]["enum"]
            return keys

        assert enum(mine) == ["aa", "bb"]
        assert enum(theirs) == ["xx", "yy"]


# --------------------------------------------------------- catalogue extension


class TestTheCatalogueSeesFilesAndPromotions:
    async def test_a_match_marks_that_the_customer_saw_products(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(db, tenant_a)

        await buscar_productos(context, {"consulta": "Product A"})

        assert context.facts_patch["vio_productos"] is True

    async def test_a_miss_that_falls_back_to_the_catalogue_does_not(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        context = _context(db, tenant_a)

        result = await buscar_productos(context, {"consulta": "nada que ver"})

        assert result.data["coincidencia"] is False
        assert "vio_productos" not in context.facts_patch

    async def test_an_empty_catalogue_does_not(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        ProductRepository(db, tenant_a.tenant_id).soft_delete(tenant_a.product_id)
        context = _context(db, tenant_a)

        result = await buscar_productos(context, {"consulta": ""})

        assert result.status == "no_disponible"
        assert "vio_productos" not in context.facts_patch

    async def test_viewing_a_product_marks_it(self, db: Session, tenant_a: TenantFixture) -> None:
        context = _context(db, tenant_a)

        await ver_producto(context, {"producto_id": str(tenant_a.product_id)})

        assert context.facts_patch["vio_productos"] is True

    async def test_viewing_a_product_of_another_business_does_not(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        context = _context(db, tenant_a)

        await ver_producto(context, {"producto_id": str(tenant_b.product_id)})

        assert "vio_productos" not in context.facts_patch

    async def test_linked_files_come_with_the_product(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _asset(
            db, tenant_a, "ficha", product_id=tenant_a.product_id, cuando_usar="Si pide detalles"
        )
        _asset(db, tenant_a, "sin_producto")
        _asset(db, tenant_b, "ajeno", product_id=tenant_b.product_id)
        context = _context(db, tenant_a)

        result = await ver_producto(context, {"producto_id": str(tenant_a.product_id)})

        assert [r["clave"] for r in result.data["recursos"]] == ["ficha"]
        assert result.data["recursos"][0]["enviado"] is False
        text = _text(result)
        assert "Recurso 'ficha' (sin enviar): Si pide detalles" in text
        assert "ajeno" not in text

    async def test_a_file_already_sent_is_shown_as_sent(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a, "ficha", product_id=tenant_a.product_id)
        context = _context(db, tenant_a, facts=Facts(recursos_enviados=["ficha"]))

        result = await buscar_productos(context, {"consulta": "Product A"})

        assert result.data["productos"][0]["recursos"][0]["enviado"] is True
        assert "ya enviado" in _text(result)

    async def test_a_file_sent_earlier_this_turn_counts_as_sent(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        _asset(db, tenant_a, "ficha", product_id=tenant_a.product_id)
        context = _context(db, tenant_a, outbound=RecordingOutbound())
        await enviar_recurso(context, {"clave": "ficha"})

        result = await ver_producto(context, {"producto_id": str(tenant_a.product_id)})

        assert result.data["recursos"][0]["enviado"] is True

    async def test_automatic_promotions_are_listed_by_scope(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        other = _product(db, tenant_a, "Otro producto", category="Ofimática")
        _promotion(db, tenant_a, "Todo con 20", valor=Decimal("20"))
        _promotion(
            db,
            tenant_a,
            "Solo el otro",
            aplica_a="productos",
            product_ids=[other.id],
            tipo="monto_fijo",
            valor=Decimal("5.50"),
        )
        _promotion(db, tenant_a, "Ofimática", aplica_a="categorias", categorias=["ofimática"])
        _promotion(db, tenant_a, "De cierre", uso="cierre", vigencia_horas=24)
        _promotion(db, tenant_a, "Vencida", termina_en=datetime.now(UTC) - timedelta(days=1))
        _promotion(db, tenant_a, "Apagada", is_active=False)
        _promotion(db, tenant_b, "De B")
        context = _context(db, tenant_a)

        mine = await ver_producto(context, {"producto_id": str(tenant_a.product_id)})
        theirs = await ver_producto(context, {"producto_id": str(other.id)})

        assert [p["nombre"] for p in mine.data["promociones_aplicables"]] == ["Todo con 20"]
        assert mine.data["promociones_aplicables"][0]["detalle"] == "20 % de descuento"
        names = sorted(p["nombre"] for p in theirs.data["promociones_aplicables"])
        assert names == ["Ofimática", "Solo el otro", "Todo con 20"]
        details = {p["nombre"]: p["detalle"] for p in theirs.data["promociones_aplicables"]}
        assert details["Solo el otro"] == "S/ 5.50 de descuento"
        assert "Promoción automática 'Todo con 20': 20 % de descuento" in _text(theirs)

    @pytest.mark.parametrize(
        ("kwargs", "detail"),
        [
            ({"tipo": "nxm", "n": 3, "m": 2, "valor": None}, "lleva 3 y paga 2"),
            ({"tipo": "precio_cantidad", "m": 5, "valor": Decimal("400")}, "5 por S/ 400.00"),
            (
                {"valor": Decimal("10"), "monto_minimo": Decimal("100")},
                "10 % de descuento (compra mínima S/ 100.00)",
            ),
        ],
    )
    async def test_every_promotion_type_reads_as_configured(
        self, db: Session, tenant_a: TenantFixture, kwargs: dict[str, Any], detail: str
    ) -> None:
        _promotion(db, tenant_a, "Promo", **kwargs)

        result = await ver_producto(
            _context(db, tenant_a), {"producto_id": str(tenant_a.product_id)}
        )

        assert result.data["promociones_aplicables"][0]["detalle"] == detail

    async def test_prices_use_the_businesss_currency(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        BusinessProfileRepository(db, tenant_a.tenant_id).upsert(currency="USD")

        result = await ver_producto(
            _context(db, tenant_a), {"producto_id": str(tenant_a.product_id)}
        )

        assert "US$ 10.00" in _text(result)
        assert "S/" not in _text(result)

    async def test_without_a_profile_prices_default_to_soles(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        result = await ver_producto(
            _context(db, tenant_a), {"producto_id": str(tenant_a.product_id)}
        )

        assert "S/ 10.00" in _text(result)

    async def test_an_unknown_currency_is_printed_as_its_code(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        BusinessProfileRepository(db, tenant_a.tenant_id).upsert(currency="CLP")

        result = await ver_producto(
            _context(db, tenant_a), {"producto_id": str(tenant_a.product_id)}
        )

        assert "CLP 10.00" in _text(result)


class TestFiltersByAnotherBusinessCategory:
    """TASK-446: a category name is not an id, but it must not open a door either."""

    async def test_the_options_list_of_b_s_category_is_empty_for_a(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _product(db, tenant_b, "Zapatilla secreta B", category="Calzado B")
        outbound = RecordingOutbound()

        result = await mostrar_opciones(
            _context(db, tenant_a, outbound=outbound),
            {"tipo": "productos", "texto": "Mira", "filtro": "Calzado B"},
        )

        shown = repr(outbound.buttons) + repr(outbound.lists)
        assert "Zapatilla secreta B" not in shown
        assert "Zapatilla secreta B" not in result.render()

    async def test_the_search_by_b_s_category_finds_nothing_of_b(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _product(db, tenant_b, "Zapatilla secreta B", category="Calzado B")

        result = await buscar_productos(
            _context(db, tenant_a), {"consulta": "zapatilla", "categoria": "Calzado B"}
        )

        assert "Zapatilla secreta B" not in result.render()
