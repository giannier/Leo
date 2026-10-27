"""How well the agent sells, not only whether the mechanics hold (feature 004 follow-up).

`sales_flows.py` checks that the process works: stages, tools, no invented amounts. This
checks the *conversation*: does the agent ask before it offers, recommend with
confidence, end each message with a next step, answer an objection instead of dropping
the price, and move toward the close when the customer shows they want to buy?

The checks are deliberately crude (patterns on the replies): they cannot judge taste, but
they catch the failures that matter and that a prompt change can fix or break, and they
make "better" a number that can be compared before and after a change. The transcripts go
in the report so a person can read them too.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from evals.sales_flows import Business, known_amounts, seed
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.providers.llm.base import LLMProvider
from app.services.turn_outbound import SandboxOutbound

PASS_RATIO = 0.75

# The catalogue of a real services business: web, AI, automation and hosting, with a
# couple of things that are really quotes. The same catalogue is run under the product
# template (what a business gets by default) and the services one.
_CATALOGUE = (
    ("Hosting web - Básico", "110.00", "Hosting", "precio_fijo", None, True, False),
    ("Hosting web - Empresarial", "250.00", "Hosting", "precio_fijo", None, True, False),
    ("Página web profesional", "1200.00", "Desarrollo", "precio_fijo", None, True, True),
    ("Sistema de gestión a medida", "2000.00", "Desarrollo", "cotizacion", None, True, False),
    ("Automatización con n8n", "800.00", "Automatización", "precio_fijo", None, True, False),
)
_DESCRIPTION = "Empresa peruana de desarrollo web, sistemas a medida, automatización e IA."
_PAYMENT = "Transferencia BCP 191-1234567-0-12 a nombre de Zentek360 SAC."


def _business(template: str) -> Business:
    return Business(
        name="Zentek360",
        industry="servicios",
        template=template,
        description=_DESCRIPTION,
        products=_CATALOGUE,
        payment=_PAYMENT,
    )


# ------------------------------------------------------------------- patterns

_MONEY = re.compile(r"S/\.?\s?(\d[\d.,]*)")


def _amounts(text: str) -> set[Decimal]:
    """Every sol amount in a text: "S/ 1200", "S/ 1,200.00" and "S/ 110.5" alike."""
    found: set[Decimal] = set()
    for raw in _MONEY.findall(text):
        raw = raw.rstrip(".,")
        if "," in raw and "." in raw:
            raw = (
                raw.replace(",", "")
                if raw.rfind(".") > raw.rfind(",")
                else raw.replace(".", "").replace(",", ".")
            )
        elif "," in raw:
            raw = (
                raw.replace(",", "")
                if re.fullmatch(r"\d{1,3}(,\d{3})+", raw)
                else raw.replace(",", ".")
            )
        try:
            found.add(Decimal(raw).quantize(Decimal("0.01")))
        except InvalidOperation:
            continue
    return found


# "¿Quieres que te cuente / te recomiende...?": offering to do the thing instead of doing it.
_PASSIVE = re.compile(
    r"¿\s*(quieres|quiere|prefieres|prefiere|deseas|desea|te gustar[ií]a|le gustar[ií]a)"
    r"\s+que\s+(te|le)\s+(cuente|explique|recomiende|detalle|muestre|diga|comparta)",
    re.IGNORECASE,
)
_HEDGE = re.compile(
    r"\b(quiz[aá]s?|tal vez|podr[ií]a ser|probablemente|supongo|generalmente|normalmente)\b",
    re.IGNORECASE,
)
_RECOMMENDS = re.compile(
    r"te recomiendo|te conviene|le recomiendo|le conviene|lo ideal|la mejor opci[oó]n|"
    r"lo que (mejor )?te sirve|te sugiero|va mejor|encaja",
    re.IGNORECASE,
)
_CLOSING = re.compile(
    r"nombre|correo|datos|pagar|pago|transferencia|yape|asesor|persona|equipo|escrib|cotizaci[oó]n|propuesta|"
    r"agendar|llamada|reuni[oó]n|te lo dejo|dejo listo|confirm|cerrar|avanz",
    re.IGNORECASE,
)
_DISCOUNT = re.compile(r"descuento|rebaja|promoci[oó]n|oferta", re.IGNORECASE)


# ------------------------------------------------------------------ scenarios


@dataclass(frozen=True)
class Check:
    name: str
    test: Callable[[list[list[str]]], bool]


@dataclass(frozen=True)
class Scenario:
    name: str
    turns: tuple[str, ...]
    checks: tuple[Check, ...] = ()


def _asks(reply: str) -> bool:
    return "?" in reply


def _ends_with_a_step(reply: str) -> bool:
    tail = reply.strip()[-220:]
    return "?" in tail or bool(_CLOSING.search(tail))


def _text(turn: list[str]) -> str:
    return " ".join(turn)


def _no_passive_offers(turns: list[list[str]]) -> bool:
    return not any(_PASSIVE.search(_text(t)) for t in turns)


def _no_hedging(turns: list[list[str]]) -> bool:
    return not any(_HEDGE.search(_text(t)) for t in turns)


def _every_reply_ends_with_a_step(turns: list[list[str]]) -> bool:
    # The first greeting may just ask what they need; every turn is held to it.
    return all(_ends_with_a_step(_text(t)) for t in turns if t)


CAFETERIA = Scenario(
    "cafetería que quiere reservas y venta en línea",
    (
        "Hola",
        "Quiero consultar sobre el anuncio de hosting que vi",
        "Si explícame porfa",
        "Mi negocio es una cafetería en Chiclayo",
        "Me interesaría que puedan reservar, y que compren mis productos también",
        "Me interesa, ¿cómo seguimos?",
    ),
    (
        Check(
            "pregunta por su necesidad antes de recomendar",
            lambda t: any(_asks(_text(x)) for x in t[1:4]),
        ),
        Check(
            "recomienda con seguridad al conocer su caso",
            lambda t: any(_RECOMMENDS.search(_text(x)) for x in t[3:5]),
        ),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
        Check("cada mensaje termina con un siguiente paso", _every_reply_ends_with_a_step),
        Check(
            "ante la señal de compra avanza al cierre",
            lambda t: bool(_CLOSING.search(_text(t[-1]))),
        ),
    ),
)

PRICE_OBJECTION = Scenario(
    "objeción de precio",
    (
        "Hola, quiero una página web para mi negocio",
        "Vendo ropa por Instagram y quiero una tienda en línea",
        "Uf, 1200 está caro",
        "Déjame pensarlo",
    ),
    (
        Check(
            "entiende la necesidad antes de ofrecer",
            lambda t: _asks(_text(t[0])) or _asks(_text(t[1])),
        ),
        Check(
            "ante 'caro' pregunta o aporta valor, no regala descuento",
            lambda t: _asks(_text(t[2])) and not _DISCOUNT.search(_text(t[2])),
        ),
        Check(
            "ante 'lo pienso' propone un paso concreto", lambda t: _ends_with_a_step(_text(t[3]))
        ),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
    ),
)

DECIDED = Scenario(
    "cliente decidido",
    (
        "Hola, necesito hosting para mi web de una academia, ¿cuánto cuesta?",
        "Tengo unas 30 visitas al día, nada más. Quiero el básico",
        "Sí, ¿cómo pago?",
    ),
    (
        Check("responde precio con seguridad y avanza", lambda t: _ends_with_a_step(_text(t[0]))),
        Check(
            "ante 'quiero el básico' cierra, no vuelve a informar",
            lambda t: bool(_CLOSING.search(_text(t[1]))),
        ),
        Check(
            "explica cómo pagar",
            lambda t: bool(re.search(r"transferencia|bcp|cuenta|pag", _text(t[2]), re.I)),
        ),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
    ),
)

UNSURE = Scenario(
    "cliente que no sabe qué necesita",
    (
        "Hola, quiero hacer crecer mi negocio con tecnología pero no sé por dónde empezar",
        "Tengo una ferretería con tres empleados, todo lo llevo en cuaderno",
        "Sí, se me pierden las cosas y no sé qué tengo en stock",
    ),
    (
        Check(
            "sondea en lugar de listar el catálogo",
            lambda t: _asks(_text(t[0])) and len(_amounts(_text(t[0]))) <= 1,
        ),
        Check(
            "conecta su dolor con una solución concreta",
            lambda t: bool(
                _RECOMMENDS.search(_text(t[2]))
                or re.search(r"sistema|gesti[oó]n|stock|inventario", _text(t[2]), re.I)
            ),
        ),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
        Check("cada mensaje termina con un siguiente paso", _every_reply_ends_with_a_step),
    ),
)

SCENARIOS = (CAFETERIA, PRICE_OBJECTION, DECIDED, UNSURE)


# Written on 2026-10-27 for the gpt-4.1-mini validation and not tuned against.
# `SALES_QUALITY_SET=fresh`.
VETERINARY = Scenario(
    "nuevo: veterinaria que quiere citas por internet",
    (
        "Buenas, tengo una veterinaria en Trujillo",
        "quiero que mis clientes puedan sacar cita por internet y ver los precios",
        "ok me convence, ¿qué necesito para empezar?",
    ),
    (
        Check(
            "pregunta por su necesidad antes de recomendar",
            lambda t: _asks(_text(t[0])) or _asks(_text(t[1])),
        ),
        Check(
            "recomienda algo concreto al conocer su caso",
            lambda t: any(
                _RECOMMENDS.search(_text(x)) or re.search(r"p[aá]gina web", _text(x), re.I)
                for x in t[1:3]
            ),
        ),
        Check(
            "ante la señal de compra avanza al cierre", lambda t: bool(_CLOSING.search(_text(t[2])))
        ),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
        Check("cada mensaje termina con un siguiente paso", _every_reply_ends_with_a_step),
    ),
)

COMPETITOR = Scenario(
    "nuevo: le cobran menos en otro lado",
    (
        "Hola, ¿cuánto cuesta una página web?",
        "es para mi estudio contable",
        "en otro lado me cobran menos",
        "ya, mándame cómo pago",
    ),
    (
        Check(
            "ante 'me cobran menos' pregunta o aporta valor, no regala descuento",
            lambda t: _asks(_text(t[2])) and not _DISCOUNT.search(_text(t[2])),
        ),
        Check(
            "explica cómo pagar cuando lo pide",
            lambda t: bool(re.search(r"transferencia|bcp|cuenta|pag|datos", _text(t[3]), re.I)),
        ),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
        Check("cada mensaje termina con un siguiente paso", _every_reply_ends_with_a_step),
    ),
)

AUTOMATION = Scenario(
    "nuevo: pierde tiempo pasando pedidos a Excel",
    (
        "Hola, pierdo mucho tiempo respondiendo pedidos por WhatsApp y pasándolos a Excel",
        "somos 2 personas, unos 40 pedidos al día",
        "me interesa, lo quiero",
    ),
    (
        Check("sondea antes de ofrecer", lambda t: _asks(_text(t[0]))),
        Check(
            "conecta su problema con la automatización",
            lambda t: any(re.search(r"automatiz|n8n", _text(x), re.I) for x in t[0:2]),
        ),
        Check("ante 'lo quiero' cierra", lambda t: bool(_CLOSING.search(_text(t[2])))),
        Check("no ofrece 'te cuento' en vez de contar", _no_passive_offers),
        Check("sin palabras de duda", _no_hedging),
    ),
)

FRESH_SCENARIOS = (VETERINARY, COMPETITOR, AUTOMATION)


# ------------------------------------------------------------------ the runner


@dataclass
class QualityOutcome:
    template: str
    scenario: Scenario
    replies: list[list[str]] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    # What the model was asked and wrote, so a run says what it cost.
    tokens_in: int = 0
    tokens_out: int = 0


async def run_scenario(
    session: Session, template: str, scenario: Scenario, *, llm: LLMProvider
) -> QualityOutcome:
    business = _business(template)
    tenant = seed(session, business)
    contact = Contact(tenant_id=tenant.id, external_user_id=f"sandbox:{uuid.uuid4()}")
    session.add(contact)
    session.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="sandbox", contact_id=contact.id, status="open"
    )
    session.add(conversation)
    session.flush()

    outcome = QualityOutcome(template, scenario)
    allowed = {a.quantize(Decimal("0.01")) for a in known_amounts(business)} | {Decimal("0.00")}
    for text in scenario.turns:
        session.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content=text,
            )
        )
        session.flush()
        pipeline = TurnPipeline(
            session,
            tenant.id,
            llm=llm,
            outbound=SandboxOutbound(session, tenant.id, conversation.id),
            sandbox=True,
            process_config=ProcessConfigService(session, cache=ProcessConfigCache()),
        )
        result = await pipeline.run(conversation.id, InboundTurn(text=text))
        if result.reply:
            outcome.tokens_in += result.reply.input_tokens
            outcome.tokens_out += result.reply.output_tokens
        said = [m.content for m in result.messages if m.content]
        outcome.replies.append(said)
        outcome.transcript.append(f"- Cliente: {text}")
        outcome.transcript.append(f"  - ({result.stage_before.value} → {result.stage_after.value})")
        outcome.transcript.extend(f"  - Leo: {line}" for line in said)
        for line in said:
            bad = _amounts(line) - allowed
            if bad:
                outcome.invented.append(f"{sorted(str(a) for a in bad)}: {line[:80]}")

    for check in scenario.checks:
        (outcome.passed if check.test(outcome.replies) else outcome.failed).append(check.name)
    return outcome


@dataclass
class QualityReport:
    outcomes: list[QualityOutcome]

    @property
    def total(self) -> int:
        return sum(len(o.passed) + len(o.failed) for o in self.outcomes)

    @property
    def score(self) -> float:
        done = sum(len(o.passed) for o in self.outcomes)
        return done / self.total if self.total else 0.0

    @property
    def invented_amounts(self) -> list[str]:
        return [item for o in self.outcomes for item in o.invented]

    @property
    def ok(self) -> bool:
        return self.score >= PASS_RATIO and not self.invented_amounts

    def render(self) -> str:
        lines = [
            f"# Calidad de venta · {self.score:.0%} ({self.total} comprobaciones)",
            f"tokens del agente: {sum(o.tokens_in for o in self.outcomes)} de entrada, "
            f"{sum(o.tokens_out for o in self.outcomes)} de salida\n",
        ]
        for outcome in self.outcomes:
            lines.append(f"## {outcome.template} · {outcome.scenario.name}")
            lines.append(f"Pasó: {len(outcome.passed)} · Falló: {len(outcome.failed)}")
            lines.extend(f"- ✗ {name}" for name in outcome.failed)
            lines.extend(f"- ⚠ monto inventado {item}" for item in outcome.invented)
            lines.append("")
            lines.extend(outcome.transcript)
            lines.append("")
        return "\n".join(lines)


async def run_quality(
    session: Session, scenarios: Sequence[Scenario], templates: Sequence[str], *, llm: LLMProvider
) -> QualityReport:
    outcomes = [
        await run_scenario(session, template, scenario, llm=llm)
        for template in templates
        for scenario in scenarios
    ]
    return QualityReport(outcomes)
