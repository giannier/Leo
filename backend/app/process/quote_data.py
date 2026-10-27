"""What a quote asks for, as the business wrote it, and the short name the assistant notes it under.

The owner writes `products.datos_a_pedir` as *questions*, one per line, each product its own:
«¿Cuántas botellas de Agua Alcalina de 625 ml necesitas?», «¿Para qué fecha necesitas el pedido?»,
«¿Es para consumo personal, un evento, tu gimnasio o tu negocio?». The rubros suggest loose phrases
(«Para cuándo lo necesitas», «Frecuencia de compra»). Neither is a name a tool can use as a field,
a date check can hang from, or a recap can print.

So each entry has a **label** («cantidad», «fecha», «distrito o dirección», «uso», «frecuencia»…):
the topic it is about when it is one of the usual ones, else the entry itself, cleaned and cut. The
tool, the conversation's `cotizacion` fact and the recap speak in labels; the business's own
question stays beside it, to be asked in the business's words.

Pure: no database, so the prompt builder, the tools and the repository share one reading.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.process.text import plain

MAX_LABEL = 40

# The usual topics, in the order they are tried: «¿Cuántas personas?» is people, not quantity, and
# «una compra puntual o recurrente» is how often, not when. The first that matches names the entry.
_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("presupuesto", re.compile(r"presupuesto")),
    ("frecuencia", re.compile(r"puntual|recurrent|frecuencia|cada cuanto|periodic")),
    (
        "fecha",
        re.compile(r"fecha|para cuando|cuando lo|que dia|\bdia\b|\bhora\b|plazo|desde cuando"),
    ),
    ("personas", re.compile(r"personas|asistentes|invitados|comensales")),
    ("cantidad", re.compile(r"cuant[oa]s|cantidad|unidades|numero de")),
    ("distrito o dirección", re.compile(r"distrito|direccion|ubicacion|zona|ciudad|\bdonde\b")),
    ("uso", re.compile(r"consumo|hogar|oficina|gimnasio|para que (lo|la|es|sera|usar)|\buso\b")),
)


# What the less obvious labels mean, for the field of the tool: «uso» alone did not make the model
# note «para mi tienda» there. «cantidad» or «fecha», or a label in the owner's words, need none.
_GLOSS: dict[str, str] = {
    "uso": "para qué lo usa",
    "frecuencia": "única o recurrente",
}


def gloss(label: str) -> str | None:
    """A few words on what a usual label stands for, or None for the owner's own wording."""
    return _GLOSS.get(plain(label))


@dataclass(frozen=True)
class QuoteDatum:
    """One thing a product asks before it is quoted: the short name and the business's wording."""

    label: str
    question: str


def _cleaned(entry: str) -> str:
    """The entry without question marks, its first letter lowered, cut at a word."""
    text = " ".join(re.sub(r"[¿?¡!]", " ", entry).split())
    if len(text) > MAX_LABEL:
        text = text[:MAX_LABEL].rsplit(" ", 1)[0]
    return text[:1].lower() + text[1:]


def topic_of(entry: str) -> str | None:
    """The usual topic an entry is about, if it is one."""
    said = plain(entry)
    return next((label for label, pattern in _TOPICS if pattern.search(said)), None)


_ASKING = re.compile(r"\b(dime|indicame|pasame|necesito|me falta|comparteme)\b")


def topics_asked(text: str) -> set[str]:
    """The usual topics a reply asks for, in its questions or where it says it needs them.

    «¿Cuántos bidones necesitas, para cuándo y en qué distrito?» asks three: a form, not a person.
    """
    asked: set[str] = set()
    for sentence in re.split(r"(?<=[.?!])\s+|\n+", text):
        said = plain(sentence)
        if "?" in sentence or _ASKING.search(said):
            asked.update(label for label, pattern in _TOPICS if pattern.search(said))
    return asked


def data_of(entries: Sequence[str]) -> list[QuoteDatum]:
    """The entries of one product as data, each under its own label.

    Two entries about the same topic («cantidad de botellas», «cantidad de cajas») do not share a
    label: the second keeps its own words, or one of the two answers would be lost.
    """
    data: list[QuoteDatum] = []
    used: set[str] = set()
    for entry in entries:
        question = " ".join(str(entry).split())
        if not question:
            continue
        label = topic_of(question)
        if label is None or plain(label) in used:
            label = _cleaned(question)
        if not label or plain(label) in used:
            continue
        used.add(plain(label))
        data.append(QuoteDatum(label, question))
    return data


def labels_of(entry_lists: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """The labels of several products, each once, in the order they first appear."""
    seen: dict[str, str] = {}
    for entries in entry_lists:
        for datum in data_of(entries):
            seen.setdefault(plain(datum.label), datum.label)
    return tuple(seen.values())
