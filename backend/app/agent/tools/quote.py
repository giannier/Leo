"""`anotar_cotizacion`: the quote the customer is putting together, and its handover.

A product «a cotizar» has no price: the assistant gathers what the business needs to quote it (the
product's `datos_a_pedir`: quantity, date, district...) and a person gives the price. Until now that
gathering lived only in the transcript. The model re-read it every turn to guess what was still
missing, so it asked again for what had been said, said again that it had the product, and decided
by mood when to hand over: the same conversation closed differently from one run to the next.

This tool is the memory and the closing rule:

* every datum the customer gives is noted here, in the turn it is given, several at a time if they
  came together; the conversation's `cotizacion` fact keeps them, and the ESTADO block shows the
  model what it already has and what is missing;
* the names of the data are the business's own, a closed list (the schema's `enum`): the customer's
  words never decide what counts as «la fecha»;
* the answer says what is missing and which one to ask next, so the next question is not the model's
  own idea; «sin dato» is accepted only for the datum it was just told to ask, never to close early;
* when nothing is missing the conversation goes to a person **from here**, with what the customer
  gave written back to them (`handover.hand_over`): the model does not decide when.

A product that asks for nothing is not handled here: the model gives it to a person itself
(`derivar_a_humano`) once it understands what the customer wants.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.agent.tools.handover import (
    MAX_QUOTE_DATA,
    MAX_SUMMARY,
    current_quote,
    customer_refused,
    hand_over,
)
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import Product
from app.process.facts import plain
from app.repositories.product import ProductRepository

MAX_NAME = 40
MAX_VALUE = 120
NO_DATA = "sin dato"
PRODUCT = "producto"

# A date and a quantity have to look like one: «para negocio» is not a date, and the model noted it
# as one (a customer answering what was not asked is normal) and closed the quote. Only these two,
# which every business asks for; any other datum is taken as the customer said it.
_DATE = re.compile(
    r"\d|lunes|martes|miercoles|jueves|viernes|sabado|domingo|\bhoy\b|manana|pasado|"
    r"\b(esta|proxima|otra|la)\s+semana|\b(este|proximo|el)\s+mes|fin de|quincena|"
    r"\bdias?\b|enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|"
    r"noviembre|diciembre|proxim|inmediat|urgente|pronto|lo antes|\bya\b|cuando"
)
_QUANTITY = re.compile(
    r"\d|\b(un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte|"
    r"treinta|cuarenta|cincuenta|cien|ciento|mil|docena|decena|varios|varias|muchos|algunos)\b"
)
_PLAUSIBLE = {"fecha": _DATE, "cantidad": _QUANTITY}
# «producto» is what the customer wants quoted, or all of it: «para negocio» (a use) and «10 de cada
# una» (a quantity) were noted as the product and reached the person that way.
_ALL_PRODUCTS = re.compile(
    r"\b(todos?|todas?|ambos|ambas|vari[oa]s|cualquiera|(los|las) (dos|tres|cuatro))\b"
)
_NAME_WORD = 4


def _quoted_products(context: ToolContext) -> list[Product]:
    rows = ProductRepository(context.session, context.tenant_id).list_active()
    return [row for row in rows if row.modo_venta == "cotizacion"]


def _required(chosen: Product | None, quoted: Sequence[Product]) -> list[str]:
    """What to ask: the chosen product's list, or the lists of all the quoted products, once each.

    Which product is noted when the customer says it, but not asked as a datum of its own: the
    business never listed it, and a customer who wants them all got the question again and again.
    """
    source = [chosen] if chosen is not None else list(quoted)
    seen: dict[str, str] = {}
    for product in source:
        for name in product.datos_a_pedir or []:
            seen.setdefault(plain(name), name)
    return list(seen.values())


def _chosen(
    context: ToolContext, arguments: dict[str, Any], quoted: Sequence[Product]
) -> Product | None:
    """The product the quote is about: the one named, the one already chosen, or the only one."""
    wanted = str(arguments.get("producto_id") or "").strip()
    before = current_quote(context)
    if not wanted and before is not None and before.producto_id:
        wanted = before.producto_id
    if wanted:
        match = next((p for p in quoted if str(p.id) == wanted), None)
        if match is not None:
            return match
    return quoted[0] if len(quoted) == 1 else None


def _canonical(name: str, required: Sequence[str]) -> str:
    """The business's spelling of a datum, if the model used another case or accents."""
    same = plain(name)
    return next((wanted for wanted in required if plain(wanted) == same), name)


def quote_key(name: str) -> str:
    """The name of a datum as a field of the tool: «distrito o dirección» → distrito_o_direccion."""
    return re.sub(r"[^a-z0-9]+", "_", plain(name)).strip("_")[:40] or "dato"


def _items(arguments: dict[str, Any], names: Sequence[str]) -> list[dict[str, str]]:
    """The data of a call as `{dato, valor}`, whether the model filled fields or sent a list."""
    listed = arguments.get("datos")
    if isinstance(listed, list):
        return [item for item in listed if isinstance(item, dict)]
    labels = {quote_key(name): name for name in (*names, PRODUCT)}
    return [
        {"dato": labels[key], "valor": str(value)}
        for key, value in arguments.items()
        if key in labels and value not in (None, "")
    ]


def _names_a_product(value: str, products: Sequence[str]) -> bool:
    """Whether `value` says one of the products (by a word of its name) or all of them."""
    said = plain(value)
    if _ALL_PRODUCTS.search(said):
        return True
    words = {w for w in re.findall(r"[a-z0-9]+", said) if len(w) >= _NAME_WORD}
    for name in products:
        for word in (w for w in re.findall(r"[a-z0-9]+", plain(name)) if len(w) >= _NAME_WORD):
            stem = word[: _NAME_WORD + 1]
            if any(w.startswith(stem) or word.startswith(w[: _NAME_WORD + 1]) for w in words):
                return True
    return False


def _given(
    raw: object,
    required: Sequence[str],
    asked: str | None,
    *,
    refused_by_customer: bool,
    products: Sequence[str] = (),
) -> tuple[dict[str, str], list[str], list[str]]:
    """What the customer gave, under the business's names; the data wrongly left «sin dato»; and
    the ones whose value is not what the datum is.

    «sin dato» closes a datum only if it is the one the assistant was just told to ask: the model
    used it to finish early (a quote went to a person without a date nobody had refused to give).
    """
    data: dict[str, str] = {}
    refused: list[str] = []
    unclear: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _canonical(" ".join(str(item.get("dato") or "").split())[:MAX_NAME], required)
        value = " ".join(str(item.get("valor") or "").split())[:MAX_VALUE]
        if not (name and value):
            continue
        if plain(value).startswith(NO_DATA):
            if asked is None or plain(name) != plain(asked) or not refused_by_customer:
                refused.append(name)
                continue
        else:
            fits = _PLAUSIBLE.get(plain(name))
            if fits is not None and not fits.search(plain(value)):
                unclear.append(name)
                continue
            if products and plain(name) == PRODUCT and not _names_a_product(value, products):
                unclear.append(name)
                continue
        data[name] = value
    return data, refused, unclear


def _summary(producto: str | None, datos: dict[str, str]) -> str:
    parts = [f"{name}: {value}" for name, value in datos.items()]
    head = f"Cotización de {producto}" if producto else "Cotización"
    return f"{head}. {'; '.join(parts)}."[:MAX_SUMMARY]


def _listed(datos: dict[str, str]) -> str:
    return "; ".join(f"{name} = {value}" for name, value in datos.items())


async def anotar_cotizacion(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    if context.conversation_id is None:
        return ToolResult.no_disponible(
            decir="No puedes anotar la cotización ahora. No lo menciones."
        )
    quoted = _quoted_products(context)
    chosen = _chosen(context, arguments, quoted)
    required = _required(chosen, quoted)
    before = current_quote(context)
    names = context.options.datos_cotizacion if context.options is not None else ()
    new, refused, unclear = _given(
        _items(arguments, names),
        required,
        before.preguntado if before else None,
        refused_by_customer=customer_refused(context),
        products=[row.name for row in quoted],
    )
    if chosen is not None and len(quoted) > 1:
        new.setdefault(PRODUCT, chosen.name)
    if not new and not refused and not unclear:
        return ToolResult.error("Falta al menos un dato con su valor (dato y valor).")

    datos = {**(before.datos if before else {}), **new}
    if len(datos) > MAX_QUOTE_DATA:
        return ToolResult.error(f"Una cotización guarda como máximo {MAX_QUOTE_DATA} datos.")
    producto = chosen.name if chosen is not None else (before.producto if before else None)
    producto_id = str(chosen.id) if chosen is not None else (before.producto_id if before else None)
    missing = [name for name in required if plain(name) not in {plain(k) for k in datos}]
    context.facts_patch["cotizacion"] = {
        "producto": producto,
        "producto_id": producto_id,
        "datos": datos,
        "pide": required,
        "preguntado": missing[0] if missing else None,
    }
    if not required:
        return ToolResult.hecho(
            texto=f"Anotado: {_listed(datos)}. Este producto no pide datos: cuando entiendas qué "
            'quiere, pásalo con derivar_a_humano(motivo="cotizacion", datos=lo anotado).'
        )
    if not missing:
        return hand_over(
            context,
            motivo="cotizacion",
            resumen=_summary(producto, datos),
            datos=datos,
            producto=producto,
        )
    warning = ""
    if refused:
        warning += (
            f"No anotes «{NO_DATA}» para {', '.join(refused)}: el cliente no dijo que no lo sabe "
            "ni que no quiere darlo. Si contestó otra cosa, reconócelo y vuelve a preguntar. "
        )
    if unclear:
        warning += (
            f"Lo que dijo no responde a {', '.join(unclear)}, así que no se anotó: reconoce lo "
            "que dijo y vuelve a preguntarlo de otra forma. "
        )
    return ToolResult.hecho(
        texto=(
            f"{warning}Anotado. Ya tienes: {_listed(datos) or 'nada aún'}. "
            f"Falta: {', '.join(missing)}.\n"
            f"Pregunta ahora SOLO por «{missing[0]}», en una frase. Confirma lo nuevo en pocas "
            "palabras; no repitas lo anterior ni digas otra vez que tienes el producto o que "
            "se cotiza."
        )
    )


def unnoted_answer(context: ToolContext, customer_message: str) -> str | None:
    """The datum the assistant asked last that the customer's message answers, if it can be told.

    Only for a date and a quantity, whose answers can be recognised without a model. The model,
    given a lone «desde el lunes», sometimes just went on to the next question without noting it
    and asked for the date again two turns later.
    """
    quote = context.facts.cotizacion
    if quote is None or quote.preguntado is None:
        return None
    fits = _PLAUSIBLE.get(plain(quote.preguntado))
    if fits is not None and fits.search(plain(customer_message)):
        return quote.preguntado
    return None
