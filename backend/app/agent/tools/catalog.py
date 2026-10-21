"""The catalogue and knowledge tools.

Agent-core v0 shipped two (Clarification 010): `consultar_catalogo` and
`consultar_conocimiento`. Feature 004 splits the first into `buscar_productos`
and `ver_producto` (`contracts/api.md` section 7) and moves the tools to the
`ToolResult` envelope. `consultar_catalogo` is kept exactly as it was, for the
v0 registry and for any conversation or prompt that still names it; in the
stage registries a call to that name is served by `buscar_productos`
(`plan.md`, "Decisiones Posteriores").

Every tool returns **prose** for the model, never bare JSON. The model reads the
result as part of its context, and prose it can quote directly produces fewer
transcription errors than a structure it has to re-serialise into a sentence -
which is where an invented price comes from.

They also say plainly when they found nothing. "No hay resultados" is a fact the
model can relay; an empty list is an absence it will be tempted to fill.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.agent.tools.registry import ToolContext, ToolRegistry, ToolResult
from app.agent.tools.shared import (
    applies_to,
    automatic,
    clip,
    currency_symbol,
    describe_promotion,
    price_text,
)
from app.models.business import Product
from app.models.media import MediaAsset
from app.models.sales_config import Promotion
from app.repositories.media import MediaAssetRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import PromotionRepository
from app.services.catalog_knowledge import search_rubro
from app.services.knowledge import retrieve

MAX_CATALOG_RESULTS = 12
MAX_KNOWLEDGE_RESULTS = 4
# Long enough to answer, short enough that four of them plus the catalogue do
# not crowd the conversation out of the context window.
MAX_CHUNK_CHARS = 700

CATALOG_TOOL = "consultar_catalogo"
SEARCH_TOOL = "buscar_productos"
VIEW_TOOL = "ver_producto"
KNOWLEDGE_TOOL = "consultar_conocimiento"
# A description in a listing; the full text is for `ver_producto`.
MAX_LISTED_DESCRIPTION = 200
# When to use a linked file, as the model reads it next to the product.
MAX_RESOURCE_HINT = 90


def _format_price(price: Decimal, currency: str) -> str:
    return f"{currency} {price:.2f}"


async def consultar_catalogo(context: ToolContext, arguments: dict[str, Any]) -> str:
    """Search the tenant's live products.

    Only active, non-deleted rows: a product the owner switched off is one they
    have stopped selling, and quoting it produces an order they cannot fill.
    """
    query = str(arguments.get("consulta") or "").strip()
    products = ProductRepository(context.session, context.tenant_id)

    criteria = [Product.deleted_at.is_(None), Product.is_active.is_(True)]
    if query:
        criteria.append(Product.name.ilike(f"%{query}%"))

    rows = products.list(*criteria, limit=MAX_CATALOG_RESULTS)
    if not rows and query:
        # Fall back to the whole catalogue so the model can offer alternatives
        # instead of dead-ending. The line below says the search missed, so it
        # does not present these as matches.
        rows = products.list(
            Product.deleted_at.is_(None), Product.is_active.is_(True), limit=MAX_CATALOG_RESULTS
        )
        if not rows:
            return "El catálogo está vacío. No hay productos que ofrecer."
        listing = _render(rows)
        return (
            f"No hay ningún producto que coincida con '{query}'. "
            f"Estos son los productos disponibles:\n{listing}"
        )

    if not rows:
        return "El catálogo está vacío. No hay productos que ofrecer."
    return f"Productos disponibles:\n{_render(rows)}"


def _render(rows: object) -> str:
    lines: list[str] = []
    for product in rows:  # type: ignore[attr-defined]
        parts = [f"- {product.name}: {_format_price(product.price, 'S/')}"]
        if product.category:
            parts.append(f"(categoría: {product.category})")
        if product.description:
            parts.append(f"- {product.description}")
        if product.stock is not None:
            # Stated only when tracked. "stock: None" means the business does
            # not count it, which is different from having none.
            parts.append(f"[stock: {product.stock}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


async def consultar_conocimiento(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Retrieve from the tenant's indexed documents.

    The query is embedded with `input_type="query"`. These models are trained
    asymmetrically, and embedding a question the same way as a passage
    measurably degrades what comes back.
    """
    query = str(arguments.get("consulta") or "").strip()
    if not query:
        return ToolResult.error("No se indicó qué buscar.")

    vectors = await context.llm.embed([query], input_type="query")
    # One copy per passage, the most trusted source first (Feature 006, C-003).
    chunks = retrieve(context.session, context.tenant_id, vectors[0], k=MAX_KNOWLEDGE_RESULTS)
    # The rubro's guidance (017, TASK-461): how to answer, never a business fact.
    guidance = search_rubro(
        context.session,
        context.rubro_version_id,
        vectors[0],
        embedding_model=context.llm.embedding_model,
    )
    if not chunks and not guidance:
        return ToolResult.no_disponible(
            texto=(
                "No hay información sobre eso en los documentos del negocio. No inventes una "
                "respuesta. Antes de decir que no lo sabes, revisa el bloque NEGOCIO (promociones, "
                "entregas, pagos) y, si es sobre lo que se vende, usa buscar_productos. Si tampoco "
                "está ahí, dilo y ofrece que lo atienda una persona; no derives sin que el cliente "
                "lo pida o lo acepte."
            )
        )

    parts: list[str] = []
    if chunks:
        passages = [f"[{chunk.title}]\n{chunk.content[:MAX_CHUNK_CHARS]}" for chunk in chunks]
        parts.append(
            "Información del negocio (de la fuente más confiable a la menos; si dos fragmentos "
            "se contradicen, vale el primero):\n" + "\n---\n".join(passages)
        )
    if guidance:
        parts.append(
            "Guía del rubro (cómo responder; los datos salen solo del negocio):\n"
            + "\n".join(f"- {hit.text}" for hit in guidance)
        )
    # The sources the answer can lean on, for the trace (006 HU-05).
    sources = list(dict.fromkeys(chunk.title for chunk in chunks))
    return ToolResult.hecho(
        data={"fragmentos": len(chunks), "guia_rubro": len(guidance), "fuentes": sources},
        texto="\n\n".join(parts),
    )


def _product_data(
    product: Product,
    *,
    full: bool = False,
    resources: Sequence[MediaAsset] = (),
    promotions: Sequence[Promotion] = (),
    sent: Sequence[str] = (),
    symbol: str = "S/",
) -> dict[str, Any]:
    description = product.description or None
    if description is not None and not full:
        description = description[:MAX_LISTED_DESCRIPTION]
    return {
        "producto_id": str(product.id),
        "nombre": product.name,
        "precio": f"{product.price:.2f}",
        "categoria": product.category,
        "descripcion": description,
        "stock": product.stock,
        "modo_venta": product.modo_venta,
        "periodo": product.periodo,
        "bundle_size": product.bundle_size,
        "destacado": product.destacado,
        "es_digital": product.es_digital,
        "datos_a_pedir": list(product.datos_a_pedir or []),
        # Files the owner linked to this product, and whether this conversation
        # already received them (`enviar_recurso` answers `omitido` if so).
        "recursos": [
            {
                "clave": asset.clave,
                "titulo": asset.titulo,
                "cuando_usar": asset.cuando_usar,
                "enviado": asset.clave in sent,
            }
            for asset in resources
        ],
        # Automatic promotions whose scope covers this product. The discount of a
        # given order is computed by the pricing module, not here.
        "promociones_aplicables": [
            {
                "promocion_id": str(promotion.id),
                "nombre": promotion.nombre,
                "detalle": describe_promotion(promotion, symbol),
            }
            for promotion in promotions
        ],
    }


def _product_line(item: dict[str, Any], symbol: str = "S/") -> str:
    """One product as text the model can quote, with its files and promotions.

    The id is there so the model can pass it to `ver_producto` and, later, to
    `gestionar_pedido`; it never needs to say it aloud.
    """
    parts = [f"- {item['nombre']} (producto_id: {item['producto_id']}):"]
    parts.append(
        price_text(
            Decimal(item["precio"]),
            symbol,
            periodo=item["periodo"],
            quoted=item["modo_venta"] == "cotizacion",
        )
    )
    if item["bundle_size"]:
        parts.append(f"[paquete de {item['bundle_size']} selecciones]")
    if item["modo_venta"] == "cotizacion":
        parts.append("[se cotiza con una persona, no tiene precio fijo]")
        if item["datos_a_pedir"]:
            parts.append(
                "[antes de derivar pide, uno por mensaje, lo que falte de: "
                + "; ".join(item["datos_a_pedir"])
                + "]"
            )
    if item["destacado"]:
        parts.append("[destacado]")
    if item["categoria"]:
        parts.append(f"(categoría: {item['categoria']})")
    if item["descripcion"]:
        parts.append(f"- {item['descripcion']}")
    if item["stock"] is not None:
        # Stated only when tracked. "stock: None" means the business does
        # not count it, which is different from having none.
        parts.append(f"[stock: {item['stock']}]")
    lines = [" ".join(parts)]
    for resource in item["recursos"]:
        state = "ya enviado en esta conversación" if resource["enviado"] else "sin enviar"
        hint = clip(resource["cuando_usar"], MAX_RESOURCE_HINT)
        lines.append(f"  Recurso '{resource['clave']}' ({state}): {hint}")
    for promotion in item["promociones_aplicables"]:
        lines.append(f"  Promoción automática '{promotion['nombre']}': {promotion['detalle']}")
    return "\n".join(lines)


def _sent_resources(context: ToolContext) -> Sequence[str]:
    """Resource keys already sent in this conversation, counting this turn's."""
    sent: Sequence[str] = context.facts_patch.get(
        "recursos_enviados", context.facts.recursos_enviados
    )
    return sent


def _enrich(
    context: ToolContext, rows: Sequence[Product], *, full: bool = False
) -> list[dict[str, Any]]:
    """Products as tool data, with the linked files and automatic promotions."""
    symbol = currency_symbol(context.session, context.tenant_id)
    assets: Mapping[uuid.UUID, Sequence[MediaAsset]] = MediaAssetRepository(
        context.session, context.tenant_id
    ).for_products([row.id for row in rows])
    promotions = automatic(
        PromotionRepository(context.session, context.tenant_id).in_force(now=datetime.now(UTC))
    )
    sent = _sent_resources(context)
    return [
        _product_data(
            row,
            full=full,
            resources=assets.get(row.id, ()),
            promotions=[p for p in promotions if applies_to(p, row)],
            sent=sent,
            symbol=symbol,
        )
        for row in rows
    ]


async def buscar_productos(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Search the tenant's live products, featured first.

    Only active, non-deleted rows: a product the owner switched off is one they
    have stopped selling, and quoting it produces an order they cannot fill.
    When nothing matches the whole catalogue is offered instead, and the text
    says the search missed so these are not presented as matches.
    """
    query = str(arguments.get("consulta") or "").strip()
    categoria = str(arguments.get("categoria") or "").strip() or None
    only_bundles = arguments.get("solo_paquetes") is True
    products = ProductRepository(context.session, context.tenant_id)

    rows = products.search_active(
        query, categoria=categoria, solo_paquetes=only_bundles, limit=MAX_CATALOG_RESULTS
    )
    if not rows and query and categoria:
        # The category is the model's guess and many businesses have none loaded: what the
        # customer said (the name) must not be cancelled by a category that matches nothing.
        rows = products.search_active(query, solo_paquetes=only_bundles, limit=MAX_CATALOG_RESULTS)
    if not rows and categoria and not query:
        rows = products.search_active(
            categoria, solo_paquetes=only_bundles, limit=MAX_CATALOG_RESULTS
        )
    narrowed = bool(query or categoria or only_bundles)
    missed = False
    if not rows and narrowed:
        rows = products.search_active(limit=MAX_CATALOG_RESULTS)
        missed = True
    if not rows:
        return ToolResult.no_disponible(
            texto="El catálogo está vacío. No hay productos que ofrecer."
        )

    items = _enrich(context, rows)
    symbol = currency_symbol(context.session, context.tenant_id)
    listing = "\n".join(_product_line(item, symbol) for item in items)
    if missed:
        asked = query or categoria or "paquetes"
        texto = (
            f"No hay ningún producto que coincida con '{asked}'. "
            f"Estos son los productos disponibles:\n{listing}"
        )
    else:
        texto = f"Productos disponibles:\n{listing}"
    if not missed:
        # The customer was shown what they asked for (a fallback listing is
        # the model offering alternatives, not the customer having seen it).
        context.facts_patch["vio_productos"] = True
    return ToolResult.hecho(data={"productos": items, "coincidencia": not missed}, texto=texto)


async def ver_producto(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """One product in full, by the id a previous search returned."""
    raw = str(arguments.get("producto_id") or "").strip()
    try:
        product_id = uuid.UUID(raw)
    except ValueError:
        return ToolResult.error("producto_id no es válido: usa el que devolvió buscar_productos.")

    product = ProductRepository(context.session, context.tenant_id).get_active(product_id)
    if product is None:
        return ToolResult.no_disponible(
            texto=(
                "No existe un producto disponible con ese producto_id. "
                "Búscalo con buscar_productos."
            )
        )
    item = _enrich(context, [product], full=True)[0]
    symbol = currency_symbol(context.session, context.tenant_id)
    context.facts_patch["vio_productos"] = True
    return ToolResult.hecho(data=item, texto="Producto:\n" + _product_line(item, symbol))


def build_default_registry() -> ToolRegistry:
    """The v0 tool set. Features 003 and 004 add to this, never replace it."""
    registry = ToolRegistry()
    registry.register(
        name=CATALOG_TOOL,
        description=(
            "Consulta los productos del negocio con sus precios y stock. "
            "Úsala siempre antes de mencionar cualquier producto o precio."
        ),
        parameters={
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Nombre o tipo de producto que busca el cliente.",
                }
            },
            "required": ["consulta"],
        },
        handler=consultar_catalogo,
    )
    registry.register(
        name=KNOWLEDGE_TOOL,
        description=(
            "Consulta la información del negocio: horarios, políticas, preguntas "
            "frecuentes, formas de entrega. Úsala antes de afirmar cualquier dato."
        ),
        parameters={
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "La duda del cliente, en sus propias palabras.",
                }
            },
            "required": ["consulta"],
        },
        handler=consultar_conocimiento,
    )
    return registry
