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

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.agent.tools.handover import latest_customer_text
from app.agent.tools.quote import match_product
from app.agent.tools.registry import ToolContext, ToolRegistry, ToolResult
from app.agent.tools.shared import (
    advisor_price_text,
    applies_to,
    automatic,
    clip,
    currency_symbol,
    describe_promotion,
    price_text,
)
from app.learning.questions import record_unanswered
from app.models.business import Product
from app.models.media import MediaAsset
from app.models.sales_config import Promotion
from app.process.quote_data import data_of
from app.process.text import FILLER_WORDS, NAME_WORD, name_tokens, plain
from app.repositories.media import MediaAssetRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import PromotionRepository
from app.services.catalog_knowledge import search_rubro
from app.services.knowledge import retrieve

MAX_CATALOG_RESULTS = 12
# The live products a phrase is matched against word by word (featured first, then by name).
MAX_SCANNED = 500
MAX_KNOWLEDGE_RESULTS = 4
# Long enough to answer, short enough that four of them plus the catalogue do
# not crowd the conversation out of the context window.
MAX_CHUNK_CHARS = 700

CATALOG_TOOL = "consultar_catalogo"
SEARCH_TOOL = "buscar_productos"
VIEW_TOOL = "ver_producto"
KNOWLEDGE_TOOL = "consultar_conocimiento"
# A description in a listing; the full text is for `ver_producto`. It was a flat 200 characters, cut
# mid-word: an owner's 450-character description lost its second half, which is where they had
# written how the product is sold («dime cuántas unidades necesitas y dónde deseas recibirlas»), and
# an edit there never reached the agent. Now the descriptions of a listing share this many
# characters, each between the two bounds, cut at a sentence or a word.
LISTED_DESCRIPTIONS_CHARS = 2_400
MIN_LISTED_DESCRIPTION = 200
MAX_LISTED_DESCRIPTION = 700
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
        if not context.sandbox:
            record_unanswered(context.session, context.tenant_id, query, context.conversation_id)
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


def cut_description(text: str, chars: int) -> str:
    """At most `chars`, ending at a sentence or a word, with «…» when something was left out."""
    flat = " ".join(text.split())
    if len(flat) <= chars:
        return flat
    head = flat[:chars]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if end >= chars // 2:
        return head[: end + 1] + " …"
    return head[: chars - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _product_data(
    product: Product,
    *,
    full: bool = False,
    resources: Sequence[MediaAsset] = (),
    promotions: Sequence[Promotion] = (),
    sent: Sequence[str] = (),
    symbol: str = "S/",
    listed_chars: int = MAX_LISTED_DESCRIPTION,
) -> dict[str, Any]:
    description = product.description or None
    if description is not None and not full:
        description = cut_description(description, listed_chars)
    return {
        "producto_id": str(product.id),
        "nombre": product.name,
        "precio": f"{product.price:.2f}",
        "categoria": product.category,
        "descripcion": description,
        "stock": product.stock,
        "tipo": product.tipo,
        "modo_venta": product.modo_venta,
        "periodo": product.periodo,
        "bundle_size": product.bundle_size,
        "bundle_categoria": product.bundle_categoria,
        "agendable": product.agendable,
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


def _product_line(
    item: dict[str, Any], symbol: str = "S/", *, quote_tool: bool = False, closings: bool = False
) -> str:
    """One product as text the model can quote, with its files and promotions.

    The id is there so the model can pass it to `ver_producto` and, later, to
    `gestionar_pedido`; it never needs to say it aloud.
    """
    parts = [f"- {item['nombre']} (producto_id: {item['producto_id']}):"]
    parts.append(
        advisor_price_text(Decimal(item["precio"]), symbol, periodo=item["periodo"])
        if item["modo_venta"] == "informar"
        else price_text(
            Decimal(item["precio"]),
            symbol,
            periodo=item["periodo"],
            quoted=item["modo_venta"] == "cotizacion",
        )
    )
    if item["bundle_size"]:
        parts.append(f"[paquete de {item['bundle_size']} selecciones]")
        if item.get("bundle_categoria"):
            # Feature 020 (D2): only items of that category complete it.
            parts.append(f"[se eligen de la categoría {item['bundle_categoria']}]")
    if closings and item.get("agendable") and item["modo_venta"] != "informar":
        # The universal process (feature 020): a bookable item closes with an appointment (CÓMO SE
        # CIERRA), whatever its price.
        parts.append("[se agenda]")
    if item["modo_venta"] == "informar":
        # Feature 020 (D7): nothing to order or quote; a person puts it together.
        parts.append(
            "[lo arma una persona del negocio: explícalo y pásalo a un asesor con "
            'derivar_a_humano(motivo="asesoria"); no va al pedido]'
        )
    if item["modo_venta"] == "cotizacion":
        parts.append("[a cotizar: sin precio fijo]")
        if item["datos_a_pedir"] and quote_tool:
            labels = [datum.label for datum in data_of(item["datos_a_pedir"])]
            parts.append(
                "[datos para cotizar, anótalos con anotar_cotizacion: " + "; ".join(labels) + "]"
            )
        elif item["datos_a_pedir"]:
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
        # Labelled: owners write greetings and selling lines into descriptions («¡Hola! Soy Nayu…»),
        # and the model must read them as what the product is, not as something to say.
        parts.append(f"Descripción (dato del producto, no la recites): {item['descripcion']}")
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
    share = LISTED_DESCRIPTIONS_CHARS // max(len(rows), 1)
    listed = min(MAX_LISTED_DESCRIPTION, max(MIN_LISTED_DESCRIPTION, share))
    return [
        _product_data(
            row,
            full=full,
            resources=assets.get(row.id, ()),
            promotions=[p for p in promotions if applies_to(p, row)],
            sent=sent,
            symbol=symbol,
            listed_chars=listed,
        )
        for row in rows
    ]


def _by_words(
    products: ProductRepository, query: str, only_bundles: bool
) -> tuple[list[Product], bool]:
    """The products that match the most words of a phrase no name contains whole, and whether
    they match every word («crema aloe» is the Crema hidratante de aloe; «pintura pared», only
    partly the paint).

    «pintura pared» found nothing and fell back to the whole catalogue (feature 020, T2.7): with
    hundreds of products the paint would not even be in the listing.
    """
    # By stems and without accents, in Python: «certificación almacén» is the Certificado en Gestión
    # de almacenes, which no `ILIKE` of either word finds.
    said = name_tokens(query)
    if not said:
        return [], False
    short = {
        w
        for w in re.findall(r"[a-z0-9]+", plain(query))
        if w.isalpha() and len(w) < NAME_WORD and w not in FILLER_WORDS
    }
    scored: list[tuple[int, Product]] = []
    for product in products.search_active(solo_paquetes=only_bundles, limit=MAX_SCANNED):
        hits = len(said & name_tokens(" ".join([product.name, *(product.tags or [])])))
        if hits:
            scored.append((hits, product))
    scored.sort(key=lambda hit: -hit[0])  # stable: featured first among equals

    def names_all(product: Product) -> bool:
        # «Paquete B» is not the Paquete A: the short words count too, as whole words.
        return short <= set(re.findall(r"[a-z0-9]+", plain(product.name)))

    every = [p for hits, p in scored if hits == len(said) and names_all(p)]
    if every:
        return every[:MAX_CATALOG_RESULTS], True
    return [product for _, product in scored][:MAX_CATALOG_RESULTS], False


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
    partial = False
    if not rows and query:
        rows, whole = _by_words(products, query, only_bundles)
        partial = bool(rows) and not whole
    narrowed = bool(query or categoria or only_bundles)
    missed = partial
    if not rows and narrowed:
        rows = products.search_active(limit=MAX_CATALOG_RESULTS)
        missed = True
    if not rows:
        return ToolResult.no_disponible(
            texto="El catálogo está vacío. No hay productos que ofrecer."
        )

    items = _enrich(context, rows)
    symbol = currency_symbol(context.session, context.tenant_id)
    quote_tool = bool(context.options and context.options.cotizar)
    listing = "\n".join(
        _product_line(item, symbol, quote_tool=quote_tool, closings=context.closings_shown)
        for item in items
    )
    if partial:
        texto = (
            f"No hay ningún producto que coincida con '{query}'. Estos son los que coinciden con "
            f"alguna de sus palabras:\n{listing}"
        )
    elif missed:
        asked = query or categoria or "paquetes"
        texto = (
            f"No hay ningún producto que coincida con '{asked}'. "
            f"Estos son los productos disponibles:\n{listing}"
        )
    else:
        texto = f"Productos disponibles:\n{listing}"
        # «bidón 20L» when the search also brought the Caja de 20 L: the customer already said which
        # one, and asking «¿cuál de los dos?» is asking again what they said.
        named = match_product(latest_customer_text(context), rows) if len(rows) > 1 else None
        if named is not None:
            texto += f"\nEl cliente ya nombró «{named.name}»: es ese, no le preguntes cuál."
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
    quote_tool = bool(context.options and context.options.cotizar)
    line = _product_line(item, symbol, quote_tool=quote_tool, closings=context.closings_shown)
    return ToolResult.hecho(data=item, texto="Producto:\n" + line)


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
