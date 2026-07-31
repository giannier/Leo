"""The two tools agent-core v0 ships with (Clarification 010).

Both return **text**, not JSON. The model reads the result as part of its
context, and prose it can quote directly produces fewer transcription errors
than a structure it has to re-serialise into a sentence - which is where an
invented price comes from.

Both also say plainly when they found nothing. "No hay resultados" is a fact
the model can relay; an empty list is an absence it will be tempted to fill.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.agent.tools.registry import ToolContext, ToolRegistry
from app.models.business import Product
from app.repositories.knowledge import KnowledgeChunkRepository
from app.repositories.product import ProductRepository

MAX_CATALOG_RESULTS = 12
MAX_KNOWLEDGE_RESULTS = 4
# Long enough to answer, short enough that four of them plus the catalogue do
# not crowd the conversation out of the context window.
MAX_CHUNK_CHARS = 700

CATALOG_TOOL = "consultar_catalogo"
KNOWLEDGE_TOOL = "consultar_conocimiento"


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


async def consultar_conocimiento(context: ToolContext, arguments: dict[str, Any]) -> str:
    """Retrieve from the tenant's indexed documents.

    The query is embedded with `input_type="query"`. These models are trained
    asymmetrically, and embedding a question the same way as a passage
    measurably degrades what comes back.
    """
    query = str(arguments.get("consulta") or "").strip()
    if not query:
        return "No se indicó qué buscar."

    vectors = await context.llm.embed([query], input_type="query")
    chunks = KnowledgeChunkRepository(context.session, context.tenant_id).search(
        vectors[0], limit=MAX_KNOWLEDGE_RESULTS
    )
    if not chunks:
        return (
            "No hay información sobre eso en los documentos del negocio. No inventes una respuesta."
        )

    passages = [chunk.content[:MAX_CHUNK_CHARS] for chunk in chunks]
    return "Información del negocio:\n" + "\n---\n".join(passages)


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
