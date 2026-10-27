"""The tools that put something in front of the customer (Feature 004, TASK-420).

* `enviar_recurso(clave)`: a file from the tenant's library.
* `mostrar_opciones(tipo, texto, filtro?)`: buttons (up to 3) or a list (up to
  10 rows) built from the database, so the model never types an option that
  does not exist.
* `elegir_camino(camino)`: records the path the customer chose by writing.

Three rules run through all of them (`contracts/api.md` section 7):

* **What was sent is remembered** in the conversation's facts, through
  `facts_patch`: `enviar_recurso` answers `omitido` instead of repeating a file,
  and the prompt's state block lists what was shown.
* **A turn has a message budget** (C-11). A tool that would exceed it sends
  nothing and answers `no_disponible`, telling the model to offer it in its next
  message.
* **WhatsApp's limits are enforced here** by truncating, so a long product name
  becomes a shorter title instead of a refused message.

The tools talk to `context.adapter` (`agent/outbound.py`) and never to a provider.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.agent.outbound import OutboundFailed
from app.agent.tools.registry import ToolContext, ToolResult
from app.agent.tools.shared import (
    automatic,
    clip,
    currency_symbol,
    describe_promotion,
    price_text,
)
from app.models.business import Product
from app.models.sales_config import Promotion
from app.providers.whatsapp.base import (
    MAX_BODY,
    MAX_BUTTON_TITLE,
    MAX_BUTTONS,
    MAX_LIST_ROWS,
    MAX_ROW_DESCRIPTION,
    MAX_ROW_TITLE,
    Button,
    ListRow,
    ListSection,
)
from app.repositories.media import MediaAssetRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import PromotionRepository

# The label of the button that opens a list (WhatsApp allows 20 characters).
LIST_BUTTON_TEXT = "Ver opciones"
BUNDLES_FILTER = "paquetes"

_NOT_SENDABLE = "No puedes enviar nada ahora. No menciones esa función."
_OVER_BUDGET = (
    "Ya enviaste suficientes mensajes en este turno. Ofrécelo en tu próximo mensaje "
    "y no digas que lo enviaste."
)
_OPTION_LABELS = {
    "caminos": "caminos",
    "categorias": "categorías",
    "productos": "productos",
    "promociones": "promociones",
}


def _remembered(context: ToolContext, key: str) -> list[str]:
    """A list fact as it stands this turn: this turn's patch over the stored value."""
    if key in context.facts_patch:
        return list(context.facts_patch[key])
    return list(getattr(context.facts, key))


def _truncate(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, keeping its line breaks."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# ------------------------------------------------------------ enviar_recurso


async def enviar_recurso(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Send a library file by its key, once per conversation."""
    clave = str(arguments.get("clave") or "").strip().lower()
    asset = MediaAssetRepository(context.session, context.tenant_id).by_clave(clave)
    if asset is None or not asset.is_active:
        return ToolResult.no_disponible(
            decir="No existe un recurso con esa clave. No lo menciones ni prometas enviarlo."
        )

    sent = _remembered(context, "recursos_enviados")
    if asset.clave in sent:
        return ToolResult.omitido(
            decir=(
                "Ese archivo ya se envió en esta conversación. "
                "No lo envíes de nuevo ni lo menciones."
            )
        )

    adapter = context.adapter
    if adapter is None:
        return ToolResult.no_disponible(decir=_NOT_SENDABLE)
    if not context.budget.can_consume():
        return ToolResult.no_disponible(decir=_OVER_BUDGET)

    try:
        await adapter.send_media(asset)
    except OutboundFailed:
        return ToolResult.error("No se pudo enviar el archivo. No digas que lo enviaste.")

    context.budget.consume()
    context.facts_patch["recursos_enviados"] = [*sent, asset.clave]
    return ToolResult.hecho(
        data={"clave": asset.clave, "tipo": asset.tipo, "titulo": asset.titulo},
        decir=(
            "El archivo ya le llegó al cliente. No lo repitas; "
            "puedes referirte a él en tu respuesta."
        ),
    )


# ---------------------------------------------------------- mostrar_opciones


def _slug(text: str) -> str:
    """An id-safe form of a category name (`Gestión pública` -> `gestion-publica`).

    Categories are free text on the product, with no id of their own, so the
    tapped option has to carry something the process engine will accept as a
    value (`[A-Za-z0-9_.-]{1,64}`).
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:48]


def _path_rows(context: ToolContext) -> list[ListRow]:
    if context.options is None:
        return []
    return [
        ListRow(f"camino:{camino.clave}", camino.titulo, camino.descripcion)
        for camino in context.options.caminos
    ]


def _category_rows(context: ToolContext) -> list[ListRow]:
    rows: list[ListRow] = []
    seen: set[str] = set()
    for category in ProductRepository(context.session, context.tenant_id).active_categories():
        slug = _slug(category)
        if slug and slug not in seen:
            seen.add(slug)
            rows.append(ListRow(f"categoria:{slug}", category))
    return rows


def _price_label(product: Product, symbol: str) -> str:
    price: Decimal = product.price
    if product.modo_venta == "cotizacion":
        return (
            "Se cotiza"
            if price <= 0
            else f"{price_text(price, symbol, periodo=product.periodo, quoted=True)} (se cotiza)"
        )
    return price_text(price, symbol, periodo=product.periodo)


def _product_rows(context: ToolContext, filtro: str | None) -> list[ListRow]:
    """Live products, featured first. `paquetes` keeps the bundles; any other
    filter is read as a category name."""
    bundles = filtro == BUNDLES_FILTER
    products = ProductRepository(context.session, context.tenant_id).search_active(
        categoria=None if bundles else filtro,
        solo_paquetes=bundles,
        limit=MAX_LIST_ROWS,
    )
    symbol = currency_symbol(context.session, context.tenant_id)
    return [
        ListRow(f"producto:{product.id}", product.name, _price_label(product, symbol))
        for product in products
    ]


def _promotion_rows(context: ToolContext) -> list[ListRow]:
    """Automatic promotions in force. Closing promotions are never listed: they
    are offered once, when the customer hesitates, through `gestionar_pedido`."""
    symbol = currency_symbol(context.session, context.tenant_id)
    promotions: list[Promotion] = automatic(
        PromotionRepository(context.session, context.tenant_id).in_force(now=datetime.now(UTC))
    )
    return [
        ListRow(
            f"promo:{promotion.id}",
            promotion.nombre,
            promotion.descripcion or describe_promotion(promotion, symbol),
        )
        for promotion in promotions
    ]


def _rows(context: ToolContext, tipo: str, filtro: str | None) -> list[ListRow]:
    if tipo == "caminos":
        return _path_rows(context)
    if tipo == "categorias":
        return _category_rows(context)
    if tipo == "productos":
        return _product_rows(context, filtro)
    return _promotion_rows(context)


async def mostrar_opciones(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Send buttons (up to 3 options) or a list (up to 10) built from the database."""
    tipo = str(arguments.get("tipo") or "")
    if tipo not in _OPTION_LABELS:
        return ToolResult.error(
            "tipo no es válido: usa caminos, categorias, productos o promociones."
        )
    if context.options_this_turn:
        # Two menus in one turn are two messages to read and two places to tap.
        return ToolResult.omitido(
            decir=(
                "Ya le enviaste opciones en este turno. No envíes otras: responde corto o no "
                "escribas nada."
            )
        )
    body = _truncate(str(arguments.get("texto") or ""), MAX_BODY)
    if not body:
        return ToolResult.error("Falta el texto que acompaña las opciones.")
    filtro = " ".join(str(arguments.get("filtro") or "").split()).lower() or None

    rows = _rows(context, tipo, filtro)
    if not rows:
        return ToolResult.no_disponible(
            decir=(
                f"No hay {_OPTION_LABELS[tipo]} para mostrar. No ofrezcas botones ni lista: "
                "pregúntale al cliente con tus palabras."
            )
        )

    adapter = context.adapter
    if adapter is None:
        return ToolResult.no_disponible(decir=_NOT_SENDABLE)
    if not context.budget.can_consume():
        return ToolResult.no_disponible(decir=_OVER_BUDGET)

    shown = rows[:MAX_LIST_ROWS]
    try:
        if len(shown) <= MAX_BUTTONS:
            await adapter.send_buttons(
                body,
                [Button(row.id, clip(row.title, MAX_BUTTON_TITLE)) for row in shown],
            )
            layout = "botones"
        else:
            await adapter.send_list(
                body,
                LIST_BUTTON_TEXT,
                [ListSection(rows=tuple(_clipped(row) for row in shown))],
            )
            layout = "lista"
    except OutboundFailed:
        return ToolResult.error("No se pudieron enviar las opciones. No digas que las enviaste.")

    context.budget.consume()
    label = tipo if filtro is None else f"{tipo}:{filtro}"
    context.options_this_turn.append(label)
    shown_before = _remembered(context, "opciones_mostradas")
    if label not in shown_before:
        context.facts_patch["opciones_mostradas"] = [*shown_before, label]
    if tipo == "productos":
        context.facts_patch["vio_productos"] = True
    return ToolResult.hecho(
        data={"tipo": tipo, "formato": layout, "mostradas": len(shown), "total": len(rows)},
        decir=(
            "El cliente ya ve las opciones en su pantalla. Tu respuesta debe ser corta o vacía; "
            "no las repitas en texto."
        ),
    )


def _clipped(row: ListRow) -> ListRow:
    """A list row inside WhatsApp's 24 and 72 character limits."""
    return ListRow(
        row.id,
        clip(row.title, MAX_ROW_TITLE),
        clip(row.description, MAX_ROW_DESCRIPTION) if row.description else None,
    )


# ------------------------------------------------------------ elegir_camino


async def elegir_camino(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Record the path the customer chose by writing it (a tap is recorded by
    `apply_interactive` without the model's help)."""
    clave = str(arguments.get("camino") or "").strip()
    camino = context.options.camino(clave) if context.options is not None else None
    if camino is None:
        return ToolResult.error("camino no es válido: usa una de las claves configuradas.")

    current = context.facts_patch.get("camino", context.facts.camino)
    if current == camino.clave:
        return ToolResult.omitido(
            decir="Ese camino ya estaba registrado. Sigue con la conversación."
        )

    context.facts_patch["camino"] = camino.clave
    return ToolResult.hecho(
        data={"camino": camino.clave, "tipo": camino.tipo},
        decir=(
            f'Quedó registrado el camino "{camino.titulo}". Sigue la conversación según ese camino.'
        ),
    )


__all__ = ["elegir_camino", "enviar_recurso", "mostrar_opciones"]
