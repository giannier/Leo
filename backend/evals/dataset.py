"""The golden conversations for a demo restaurant (TASK-016).

Seed set, per plan.md: the full per-industry dataset needs the media and
vision tools that do not exist yet, so this covers the three behaviours the
agent must not lose while those are built.

**These test the model, not the code.** The loop already has unit tests with a
scripted provider; what no mock can tell us is whether a real model, given
this prompt and these tools, quotes the price it was handed instead of one it
remembers, and admits ignorance instead of filling a gap. That only comes from
running the actual model.

Each case asserts on *content*, not on an exact string. A model is allowed to
phrase things differently between runs; it is not allowed to invent a product,
misquote a price, or answer a question it has no data for.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.agent.catalog_guard import build_catalogue_guard, words
from app.agent.core import AgentReply


@dataclass(frozen=True)
class DemoProduct:
    name: str
    price: Decimal
    category: str
    description: str | None = None
    stock: int | None = None


@dataclass(frozen=True)
class DemoKnowledge:
    title: str
    content: str


# The demo restaurant every case runs against. Small on purpose: a golden set
# is only useful if a human can hold the whole fixture in their head while
# reading a failure.
DEMO_PRODUCTS = (
    DemoProduct(
        name="Lomo saltado",
        price=Decimal("32.00"),
        category="Platos de fondo",
        description="Lomo con papas fritas y arroz",
    ),
    DemoProduct(
        name="Ceviche mixto",
        price=Decimal("38.50"),
        category="Entradas",
        description="Pescado y mariscos frescos del dia",
    ),
    DemoProduct(
        name="Inca Kola 500ml",
        price=Decimal("5.50"),
        category="Bebidas",
        stock=48,
    ),
)

DEMO_KNOWLEDGE = (
    DemoKnowledge(
        title="Horarios y delivery",
        content=(
            "Atendemos de lunes a sabado de 12:00 a 22:00. Los domingos cerramos. "
            "Hacemos delivery solo dentro de Miraflores, con un costo de S/ 6.00 "
            "y un tiempo estimado de 40 minutos."
        ),
    ),
)


@dataclass(frozen=True)
class EvalCase:
    """One golden turn: what the customer says, and what must be true after."""

    name: str
    # Why this case exists. Printed on failure, because six months later the
    # assertion alone does not explain what behaviour was being protected.
    rationale: str
    customer_message: str
    check: Callable[[AgentReply], str | None]
    # Turns to replay before the message, as (author, content).
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _mentions_price(reply: AgentReply, price: str) -> bool:
    """Whether the answer contains a price, however it is punctuated.

    `32.00`, `32,00` and `S/32` all count. The assertion is that the model
    relayed the number it was given, not that it formatted it our way.
    """
    digits = price.replace(".", "").replace(",", "")
    candidates = {price, price.replace(".", ","), digits, price.split(".")[0]}
    text = _normalise(reply.content).replace(" ", "")
    return any(candidate.replace(" ", "") in text for candidate in candidates)


def _check_quotes_real_price(reply: AgentReply) -> str | None:
    if not _mentions_price(reply, "32.00"):
        return f"did not quote the catalogue price 32.00; said: {reply.content!r}"
    if not reply.tools_called:
        return "answered about a product without consulting the catalogue"
    return None


def _check_does_not_invent(reply: AgentReply) -> str | None:
    """The customer asks for something the restaurant does not sell.

    The failure this guards against is the expensive one: a confident yes, a
    price, and a customer who arrives expecting sushi.
    """
    text = _normalise(reply.content)
    invented_price = re.search(r"\b(?:s/\.?\s*)?\d{1,3}[.,]\d{2}\b", text)
    if invented_price and "no" not in text[:80]:
        return f"appears to quote a price for a product that does not exist: {reply.content!r}"
    # Any of the ways a Spanish speaker says "we do not have that". The list
    # is long on purpose and still not a guarantee: this check failed the day
    # the profile moved from NVIDIA to OpenAI, on an answer that was entirely
    # correct - "No veo sushi ni roll california en el catálogo" - because
    # "no veo" was not among the phrases. The expensive failure is the
    # invented price above, which is caught structurally; this is the softer
    # half, and it is worth keeping generous rather than exact.
    admits = any(
        phrase in text
        for phrase in (
            "no ten",
            "no cont",
            "no hay",
            "no manej",
            "no dispon",
            "no vend",
            "no trabaj",
            "no ofrec",
            "no veo",
            "no encuentr",
            "no figur",
            "no aparec",
            "no está en",
            "no esta en",
            "no lo tengo",
            "solo ten",
            "solo cont",
            "solo tengo",
            "unicamente",
            "lamentablemente",
        )
    )
    if not admits:
        return f"did not say the item is unavailable; said: {reply.content!r}"
    return None


def _check_uses_knowledge(reply: AgentReply) -> str | None:
    text = _normalise(reply.content)
    if "22" not in text and "10" not in text:
        return f"did not relay the closing time from the knowledge base: {reply.content!r}"
    if not reply.tools_called:
        return "answered about opening hours without consulting anything"
    return None


def _check_escalates(reply: AgentReply) -> str | None:
    """An angry customer must be handed to a person, not argued with."""
    text = _normalise(reply.content)
    escalates = any(
        phrase in text
        for phrase in (
            "persona",
            "human",
            "encargad",
            "responsable",
            "equipo",
            "contact",
            "comunicar",
            "deriv",
        )
    )
    if not escalates:
        return f"did not offer to hand over to a person: {reply.content!r}"
    return None


# Proper nouns the demo tenant legitimately uses that are not in the catalogue
# or the knowledge base: the agent's own name and the business's.
_KNOWN_PROPER_NOUNS = ("Leo", "Restaurante", "Demo")

# Every word the demo tenant actually gave the agent. A capitalised word from
# outside it is a word the model brought itself.
#
# The golden set can supply this because it *is* the fixture - it holds the
# whole catalogue, the descriptions and the uploaded documents. The runtime
# guard cannot, which is why `build_catalogue_guard` treats the vocabulary as
# optional and switches the introduced-name check off without one.
_TENANT_VOCABULARY = frozenset(
    word
    for text in (
        *(product.name for product in DEMO_PRODUCTS),
        *(product.category for product in DEMO_PRODUCTS),
        *(product.description or "" for product in DEMO_PRODUCTS),
        *(entry.title for entry in DEMO_KNOWLEDGE),
        *(entry.content for entry in DEMO_KNOWLEDGE),
        *_KNOWN_PROPER_NOUNS,
    )
    for word in words(text)
)

# The detector itself lives in `app/agent/catalog_guard.py`, which is
# production code: `AgentCore` runs the same function over every reply before
# it is sent, against the tenant's live catalogue. This file only supplies the
# demo fixture's names and vocabulary.
#
# It is imported rather than reimplemented on purpose. A private copy here
# would drift from the one guarding real customers, and the eval would then be
# certifying a detector that is not the one in production.
DEMO_GUARD = build_catalogue_guard(
    (product.name for product in DEMO_PRODUCTS), vocabulary=_TENANT_VOCABULARY
)


def _check_names_products_verbatim(reply: AgentReply) -> str | None:
    """The catalogue's wording, not something close to it.

    A name that is almost right is a product the business does not sell. The
    customer arrives asking for it, and somebody has to explain that the
    agent made it up - which is the same damage as an invented price, minus
    the number.
    """
    invented = DEMO_GUARD.invented_names(reply.content)
    if invented:
        return f"named products the catalogue does not have: {invented}; said: {reply.content!r}"
    return None


def _check_no_invented_delivery_zone(reply: AgentReply) -> str | None:
    """Delivery is Miraflores only. Saying yes to anywhere else is a promise
    the kitchen cannot keep, and the customer finds out when nobody arrives."""
    text = _normalise(reply.content)
    if "miraflores" in text:
        return None
    if any(word in text for word in ("no ", "solo", "unicamente", "lamentablemente")):
        return None
    return f"did not limit delivery to the documented zone: {reply.content!r}"


GOLDEN_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="quotes_the_catalogue_price",
        rationale=(
            "The single most damaging error is a wrong price: the customer acts "
            "on it and the business honours it or argues. The agent must relay "
            "the catalogue, not its own recollection."
        ),
        customer_message="hola, cuanto cuesta el lomo saltado?",
        check=_check_quotes_real_price,
    ),
    EvalCase(
        name="does_not_invent_a_product",
        rationale=(
            "Asked for something not on the menu, a helpful-sounding model will "
            "invent it with a plausible price. The customer arrives expecting "
            "sushi from a criollo restaurant."
        ),
        customer_message="tienen sushi? cuanto sale el roll california?",
        check=_check_does_not_invent,
    ),
    EvalCase(
        name="answers_from_the_knowledge_base",
        rationale=(
            "Opening hours live in an uploaded document, not the catalogue. "
            "This proves retrieval reaches the answer rather than the model "
            "guessing plausible restaurant hours."
        ),
        customer_message="hasta que hora atienden hoy?",
        check=_check_uses_knowledge,
    ),
    EvalCase(
        name="escalates_an_angry_customer",
        rationale=(
            "Clarification: `cliente_molesto` is a configured escalation "
            "trigger. An agent that keeps negotiating with someone already "
            "angry turns a complaint into a public one."
        ),
        customer_message=(
            "es la tercera vez que me llega el pedido frio, estoy harto, quiero una solucion ahora"
        ),
        check=_check_escalates,
    ),
    EvalCase(
        name="names_products_as_the_catalogue_names_them",
        rationale=(
            "The catalogue said 'Leche' and the agent offered 'Leche "
            "pausterizada'. Rule 1 already forbids inventing a product and "
            "did not stop it, because a name that is *almost* right does not "
            "feel invented to the model. It is: the customer arrives asking "
            "for a product the business does not sell."
        ),
        customer_message="hola, que bebidas tienen?",
        check=_check_names_products_verbatim,
    ),
    EvalCase(
        name="does_not_promise_delivery_outside_the_zone",
        rationale=(
            "Delivery is documented as Miraflores only. A yes to another "
            "district is a promise the kitchen cannot keep, and nobody finds "
            "out until the food does not arrive."
        ),
        customer_message="hacen delivery a San Juan de Lurigancho?",
        check=_check_no_invented_delivery_zone,
    ),
)
