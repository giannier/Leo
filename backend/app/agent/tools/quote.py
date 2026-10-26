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
* the answer says what is known and what is missing, and leaves the conversation to the model: a
  person answers what was said first, follows up a vague answer and asks the rest in their own
  words (a first version dictated «ask ONLY this, in one sentence», and both gpt-5.4-mini and
  gpt-5.4 then answered like a form: «Anotado: *8 cajas*. ¿Para qué fecha...?»); «sin dato» is
  accepted only when the customer refuses it, one per call, never to close early;
* when nothing is missing the conversation goes to a person **from here**, with what the customer
  gave written back to them (`handover.hand_over`): the model does not decide when.

A product that asks for nothing is not handled here: the model gives it to a person itself
(`derivar_a_humano`) once it understands what the customer wants.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Any

from app.agent.tools.handover import (
    _REFUSAL,
    MAX_QUOTE_DATA,
    MAX_SUMMARY,
    current_quote,
    customer_refused,
    hand_over,
)
from app.agent.tools.registry import ToolContext, ToolResult
from app.models.business import Product
from app.process.quote_data import QuoteDatum, data_of, topic_of
from app.process.text import plain
from app.repositories.conversation import MessageRepository
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
_PLAUSIBLE = {"fecha": _DATE, "cantidad": _QUANTITY, "personas": _QUANTITY}
# «producto» is what the customer wants quoted, or all of it: «para negocio» (a use) and «10 de cada
# una» (a quantity) were noted as the product and reached the person that way.
_ALL_PRODUCTS = re.compile(
    r"\b(todos?|todas?|ambos|ambas|vari[oa]s|cualquiera|(los|las) (dos|tres|cuatro))\b"
)
_NAME_WORD = 4
LAST_HEARD = 40


def _quoted_products(context: ToolContext) -> list[Product]:
    rows = ProductRepository(context.session, context.tenant_id).list_active()
    return [row for row in rows if row.modo_venta == "cotizacion"]


def _data(chosen: Product | None, quoted: Sequence[Product]) -> list[tuple[QuoteDatum, str]]:
    """What to ask: the chosen product's data, or those of all the quoted products, once each.

    Each comes with the business's own wording when it is the same wherever it is asked (or the
    product is chosen): «¿Cuántas botellas de 500 ml necesitas?» is for that product only.

    Which product is noted when the customer says it, but not asked as a datum of its own: the
    business never listed it, and a customer who wants them all got the question again and again.
    """
    source = [chosen] if chosen is not None else list(quoted)
    found: dict[str, list[QuoteDatum]] = {}
    for product in source:
        for datum in data_of(list(product.datos_a_pedir or [])):
            found.setdefault(plain(datum.label), []).append(datum)
    out: list[tuple[QuoteDatum, str]] = []
    for same in found.values():
        wordings = {plain(d.question) for d in same}
        own = same[0].question if len(wordings) == 1 else ""
        # «fecha» written as «fecha» says nothing more; only a question adds something.
        out.append((same[0], "" if plain(own) == plain(same[0].label) else own))
    return out


def _required(chosen: Product | None, quoted: Sequence[Product]) -> list[str]:
    return [datum.label for datum, _ in _data(chosen, quoted)]


def _tokens(text: str) -> set[str]:
    """The stems of a product name: 4 letters of a word, or a number («20»)."""
    spaced = re.sub(r"(\d)([a-z])", r"\1 \2", re.sub(r"([a-z])(\d)", r"\1 \2", plain(text)))
    return {
        w[:_NAME_WORD] if w.isalpha() else w
        for w in re.findall(r"[a-z0-9]+", spaced)
        if (w.isalpha() and len(w) >= _NAME_WORD) or w.isdigit()
    }


def match_product(value: str, products: Sequence[Product]) -> Product | None:
    """The one product `value` names, if it names exactly one of `products`.

    Only the words that tell the products apart count: «bidón de 20 L» names the Bidón when the
    other one is the Caja de 20 L (both have «agua» and «20»), «agua de 20 L» names neither.
    """
    if not products:
        return None
    names = [_tokens(p.name) for p in products]
    shared = set.intersection(*names) if len(names) > 1 else set()
    said = _tokens(value)
    scores = [len(said & (tokens - shared)) for tokens in names]
    best = max(scores)
    winners = [p for p, score in zip(products, scores, strict=True) if score == best]
    return winners[0] if best > 0 and len(winners) == 1 else None


def _chosen(
    context: ToolContext,
    arguments: dict[str, Any],
    quoted: Sequence[Product],
    heard: str | None = None,
) -> Product | None:
    """The product the quote is about: the one named now, the one chosen before, or the only one."""
    before = current_quote(context)
    named = str(arguments.get(PRODUCT) or "").strip()
    listed = arguments.get("datos")
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, dict) and plain(str(item.get("dato") or "")) == PRODUCT and not named:
            named = str(item.get("valor") or "").strip()
    if named:
        match = match_product(named, quoted)
        if match is not None:
            return match
    if before is not None and before.producto_id:
        match = next((p for p in quoted if str(p.id) == before.producto_id), None)
        if match is not None:
            return match
    if len(quoted) == 1:
        return quoted[0]
    # The model did not say which, but the customer did («bidones de 20 litros»).
    return match_product(heard, quoted) if heard else None


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


def _asked_last(context: ToolContext) -> set[str]:
    """The labels of the data the assistant asked for in its last message (it may ask two at once).

    The datum asked is answered in the customer's words, whatever the model calls it («una sola
    vez» → «única»); it is the ones nobody asked that get invented.
    """
    if context.conversation_id is None:
        return set()
    rows = MessageRepository(context.session, context.tenant_id).list_for_conversation(
        context.conversation_id
    )
    last = next((m.content for m in reversed(list(rows)) if m.direction == "outbound"), "")
    asked: set[str] = set()
    for sentence in re.split(r"(?<=[.?!])\s+|\n+", last):
        if "?" in sentence or re.search(
            r"\b(dime|ind[ií]came|p[aá]same|necesito)\b", sentence, re.I
        ):
            topic = topic_of(sentence)
            if topic is not None:
                asked.add(plain(topic))
    return asked


def _heard(context: ToolContext) -> str | None:
    """What the customer has written, without accents: the only thing a datum may come from.

    None when there is nothing written to compare with (a tool run outside a conversation).
    """
    if context.conversation_id is None:
        return None
    rows = MessageRepository(context.session, context.tenant_id).list_for_conversation(
        context.conversation_id
    )
    said = " ".join(m.content for m in list(rows)[-LAST_HEARD:] if m.author == "customer")
    return plain(said) or None


def _grounded(value: str, heard: str) -> bool:
    """Whether the customer said what `value` says: a word's first letters, or a number, in common.

    The model filled «frecuencia: recurrente» (or «mensual», from «30 al mes») for a customer who
    had not said it, to close the quote sooner, and the person got a datum nobody gave.
    """
    said = plain(value)
    words = {w[:_NAME_WORD] for w in re.findall(r"[a-z]+", said) if len(w) >= _NAME_WORD}
    numbers = set(re.findall(r"\d+", said))
    if not words and not numbers:
        return True
    if words & {w[:_NAME_WORD] for w in re.findall(r"[a-z]+", heard) if len(w) >= _NAME_WORD}:
        return True
    return bool(numbers & set(re.findall(r"\d+", heard)))


# What is said about what the order is for, not where it goes: «para mi hogar» answered the address
# question and the model filed it there, then asked for the use three more times.
_USES = frozenset(
    {
        "hogar", "casa", "oficina", "negocio", "tienda", "bodega", "restaurante", "gimnasio",
        "empresa", "local", "evento", "consumo", "personal", "familia",
    }
)  # fmt: skip
_FILLER = frozenset(
    {"mi", "mis", "tu", "su", "la", "el", "en", "para", "de", "del", "un", "una", "es"}
)


def _is_a_use(value: str) -> bool:
    """Whether `value` only says what the order is for («mi hogar», «para el negocio»)."""
    words = [w for w in re.findall(r"[a-z]+", plain(value)) if w not in _FILLER]
    return bool(words) and all(w in _USES for w in words)


def _given(
    raw: object,
    required: Sequence[str],
    asked: str | Collection[str] | None,
    *,
    refused_by_customer: bool,
    products: Sequence[str] = (),
    heard: str | None = None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """What the customer gave, under the business's names; the data wrongly left «sin dato»; and
    the ones whose value is not what the datum is.

    «sin dato» closes a missing datum only when the customer's own words refuse it («todavía no
    sé»),
    and one per call: the model used it to finish early (a quote went to a person without a date
    nobody had refused to give).
    """
    asked_labels = (
        set()
        if asked is None
        else {plain(asked)}
        if isinstance(asked, str)
        else {plain(a) for a in asked}
    )
    data: dict[str, str] = {}
    refused: list[str] = []
    unclear: list[str] = []
    closed = False  # one «sin dato» per call: two at once is closing the quote early
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _canonical(" ".join(str(item.get("dato") or "").split())[:MAX_NAME], required)
        value = " ".join(str(item.get("valor") or "").split())[:MAX_VALUE]
        if not (name and value):
            continue
        if plain(name) == "distrito o direccion" and _is_a_use(value):
            use = next((r for r in required if plain(r) == "uso"), None)
            if use is None:
                unclear.append(name)  # a use, not an address, and the business asks no use
                continue
            name = use
        if plain(value).startswith(NO_DATA):
            wanted = plain(name) in {plain(r) for r in required}
            if not (refused_by_customer and wanted) or closed:
                refused.append(name)
                continue
            closed = True
        else:
            fits = _PLAUSIBLE.get(plain(name))
            if fits is not None and not fits.search(plain(value)):
                unclear.append(name)
                continue
            if products and plain(name) == PRODUCT and not _names_a_product(value, products):
                unclear.append(name)
                continue
            # The datum just asked is answered in the customer's words, whatever the model calls it
            # («una sola vez» → «única»); it is the ones nobody asked that get invented.
            just_asked = plain(name) in asked_labels
            free = plain(name) != PRODUCT and plain(name) not in _PLAUSIBLE and not just_asked
            if heard is not None and free and not _grounded(value, heard):
                unclear.append(name)
                continue
        data[name] = value
    return data, refused, unclear


def _with_wording(missing: Sequence[str], wording: dict[str, str]) -> list[str]:
    """What is missing, each with the business's question when it is the same wherever it is asked,
    so the model can see whether the customer already answered it («uso» → «¿Es para tu hogar...?»).
    """
    return [
        f"{name} ({wording[plain(name)]})" if wording.get(plain(name)) else name for name in missing
    ]


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
    heard = _heard(context)
    chosen = _chosen(context, arguments, quoted, heard)
    required = _required(chosen, quoted)
    before = current_quote(context)
    names = context.options.datos_cotizacion if context.options is not None else ()
    new, refused, unclear = _given(
        _items(arguments, names),
        required,
        _asked_last(context) | ({before.preguntado} if before and before.preguntado else set()),
        refused_by_customer=customer_refused(context),
        products=[row.name for row in quoted],
        heard=heard,
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
    if len(missing) == 1 and customer_refused(context) and not (refused or unclear):
        # «todavía no sé la fecha» and nothing else is missing: it is «sin dato», whether or not the
        # model remembered to say so. The answer it gave was «lo dejo pendiente» and the quote never
        # went to a person.
        datos[missing[0]] = NO_DATA
        missing = []
    wording = {plain(datum.label): question for datum, question in _data(chosen, quoted)}
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
            f"No se anotó {', '.join(unclear)}: no coincide con lo que escribió el cliente. Si "
            "lo dijo, anótalo con sus palabras; si no, pregúntaselo y no lo supongas. "
        )
    return ToolResult.hecho(
        texto=(
            f"{warning}Datos de la cotización. Ya tienes: {_listed(datos) or 'nada aún'}. "
            f"Falta: {', '.join(_with_wording(missing, wording))}.\n"
            "Contesta como lo haría una persona atenta, no como un formulario: primero lo que el "
            "cliente dijo o preguntó (el precio lo da una persona al final). Si su respuesta es "
            "vaga o no encaja con lo que preguntaste («mi hogar» cuando pedías la dirección), "
            "pregunta lo que necesitas para entenderla («¿en qué distrito queda?»). Si ya dijo "
            "algo de lo que falta, anótalo en vez de preguntarlo. Después sigue con lo que falta, "
            "con tus palabras: puedes juntar dos preguntas que van juntas. No le recites de "
            "vuelta lo que acaba de decir ni lo que ya sabes."
        )
    )


_ACKNOWLEDGEMENTS = frozenset(
    {
        "ok",
        "okey",
        "gracias",
        "dale",
        "listo",
        "si",
        "no",
        "hola",
        "buenas",
        "bueno",
        "vale",
        "claro",
    }
)


def unnoted_answer(context: ToolContext, customer_message: str) -> str | None:
    """What the customer's message may have answered that the model did not note, if it can be told.

    A date or a quantity can be recognised without a model; so can a refusal («todavía no sé la
    fecha»). Anything else in a message that is not a question and not a plain «gracias» may be a
    datum too («para mi hogar»): the model understood it enough to answer, did not note it and
    asked for it again three turns in a row. Returns the datum, or those still missing.
    """
    quote = context.facts.cotizacion
    if quote is None or quote.preguntado is None:
        return None
    said = plain(customer_message)
    fits = _PLAUSIBLE.get(plain(quote.preguntado))
    # «todavía no sé la fecha» answers the question too: it is «sin dato», and it has to be noted.
    if (fits is not None and fits.search(said)) or _REFUSAL.search(said):
        return quote.preguntado
    words = [w for w in re.findall(r"[a-z0-9]+", said) if w not in _ACKNOWLEDGEMENTS]
    if quote.falta and words and "?" not in customer_message:
        return ", ".join(quote.falta)
    return None
