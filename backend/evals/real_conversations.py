# ruff: noqa: E501
"""Conversations that went wrong in the simulator, replayed against the real model.

`sales_flows.py` checks that the process works (stage reached, tools used, no invented amount) and
`sales_quality.py` that replies end with a next step. Both passed while the owner, in the simulator,
read an assistant that said «Sí, contamos con…» in every message, asked again what the customer had
just answered, justified a quote with a sales pitch that was nowhere in the catalogue, and closed in
a different way each time. They passed because they did not look at *the conversation as the customer
lives it*, and because one of them asked for a question at the end of every reply, which is how the
loop is made.

This module takes the conversations the owner pasted (a water wholesaler that quotes: bidones and
bottles, price to be quoted by a person) and checks, turn by turn and against what each customer
message actually gave, the things that make a conversation unreasonable:

* it does not ask again for something the customer already gave (in any earlier message, or in the
  same one);
* once it said the product is available it does not say it again unless they ask again;
* it does not make up reasons («alta rotación», «te conviene…») that no tool or catalogue holds;
* when everything it needs is given, the conversation goes to a person in that same turn, with what
  the customer said written back to them, and not before;
* a correction («ya te dije que…») is taken, not answered with the same question.

The customer lines are the owner's own. Which data each line gives is declared here, so the checks do
not guess. What this cannot judge (tone, taste) goes to the optional judge, which reads the whole
transcript and says if a person would find it reasonable; it is reported, and gates when `JUDGE=gate`.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from evals.sales_flows import Business, seed
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.templates.base import Stage
from app.providers.llm.base import ChatMessage, LLMProvider
from app.repositories.product import ProductRepository
from app.services.turn_outbound import SandboxOutbound

# Measured on 20 runs of the 18 scenarios with the real model: 16 to 18 pass almost always (89 % to
# 100 %), one run gave 13 (72 %). A bar of 90 % turns red on a single scenario's luck; 85 % does not,
# and the failure it exists to catch (the first version of this assistant passed 1 of 9) is far below.
PASS_RATIO = 0.85


# ------------------------------------------------------------------ the business


def _water(asks: Sequence[str]) -> Callable[[Session, object], None]:
    """A water wholesaler: three presentations, all «a cotizar», asking for `asks` before quoting."""

    def setup(session: Session, tenant) -> None:  # type: ignore[no-untyped-def]
        config = session.query(AgentConfig).filter(AgentConfig.tenant_id == tenant.id).one()
        config.capacidades = ["atender", "cotizar"]
        config.agent_name = "Nayu"
        for product in ProductRepository(session, tenant.id).list():
            product.datos_a_pedir = list(asks)

    return setup


def water(asks: Sequence[str]) -> Business:
    return Business(
        name="Bebidas yanayacu",
        industry="b2b",
        template="servicios",
        description="Distribuidora de agua de mesa en bidones y botellas, por mayor y menor, en Lima.",
        products=(
            ("Agua de mesa 500 ml", "0.00", "Agua", "cotizacion", None, False, False),
            ("Agua de mesa 7 L", "0.00", "Agua", "cotizacion", None, False, False),
            ("Bidón de agua de mesa 20 L", "0.00", "Agua", "cotizacion", None, False, True),
        ),
        payment="Transferencia a nombre de Bebidas yanayacu.",
        setup=_water(asks),  # type: ignore[arg-type]
    )


# Bebidas yanayacu as it is in production (read 2026-10-26 with the owner's permission): five products
# «a cotizar», each with the owner's own questions, and the capabilities atender, vender, cotizar and
# captar. Nothing else about the business is copied: no customers, no prices, no credentials.
NAYU_ASKS: dict[str, tuple[str, ...]] = {
    "Agua Alcalina": (
        "¿Cuántas botellas de Agua Alcalina de 625 ml necesitas?",
        "¿Para qué fecha necesitas tu pedido?",
        "¿A qué distrito o dirección deseas recibirlo?",
        "¿Es para consumo personal, un evento, tu gimnasio o tu negocio?",
        "¿Necesitas una compra puntual o pedidos recurrentes?",
    ),
    "Agua de Mesa de 625 ml": (
        "¿Cuántas botellas de 500 ml necesitas?",
        "¿Para qué fecha necesitas el pedido?",
        "¿A qué distrito o dirección se realizará la entrega?",
        "¿Es para consumo personal, un evento o tu negocio?",
    ),
    "Agua de Mesa de 7 L": (
        "¿Cuántas unidades de agua de 7 litros necesitas?",
        "¿Para qué fecha necesitas tu pedido?",
        "¿A qué distrito o dirección deseas recibirlo?",
        "¿Es para tu hogar, oficina o negocio?",
    ),
    "Bidon de Agua de 20L": (
        "¿Cuántas unidades de agua de 20 litros necesitas?",
        "¿Para qué fecha necesitas el pedido?",
        "¿A qué distrito o dirección deseas recibirlo?",
        "¿Es para tu hogar, oficina o negocio?",
        "¿Necesitas una entrega puntual o pedidos recurrentes?",
    ),
    "Caja de Agua de 20L": (
        "¿Cuántas unidades de agua de 20 litros necesitas?",
        "¿Para qué fecha necesitas el pedido?",
        "¿A qué distrito o dirección deseas recibirlo?",
        "¿Es para tu hogar, oficina o negocio?",
        "¿Necesitas una entrega puntual o pedidos recurrentes?",
    ),
}


def _nayu_setup(session: Session, tenant) -> None:  # type: ignore[no-untyped-def]
    config = session.query(AgentConfig).filter(AgentConfig.tenant_id == tenant.id).one()
    config.capacidades = ["atender", "vender", "cotizar", "captar"]
    config.agent_name = "Nayu"
    for product in ProductRepository(session, tenant.id).list():
        product.datos_a_pedir = list(NAYU_ASKS[product.name])


def nayu() -> Business:
    return Business(
        name="Bebidas yanayacu",
        industry="b2b",
        template="servicios",
        description="Distribuidora de agua de mesa en bidones y botellas, por mayor y menor.",
        products=tuple((name, "0.00", "", "cotizacion", None, False, False) for name in NAYU_ASKS),
        payment="Transferencia a nombre de Bebidas yanayacu.",
        setup=_nayu_setup,  # type: ignore[arg-type]
    )


# What the assistant may ask for, in the words it uses. A reply «asks for» a datum when a sentence
# of it that is a question, or says it is missing, matches.
ASKS: dict[str, re.Pattern[str]] = {
    "cantidad": re.compile(r"cu[aá]nt[oa]s?\b|cantidad|unidades|cu[aá]ntos bidones", re.I),
    "fecha": re.compile(
        r"qu[eé] fecha|fecha (exacta|de entrega|que|necesit|quier)|cu[aá]ndo|para qu[eé] d[ií]a|"
        r"me falta (solo )?(la )?fecha",
        re.I,
    ),
    "distrito": re.compile(r"distrito|direcci[oó]n|d[oó]nde|zona de entrega", re.I),
    "uso": re.compile(
        r"hogar,? (oficina|o )|oficina,? o|consumo (personal|propio)|"
        r"para qu[eé] (lo|la|es|ser[aá]|usar)|tipo de (negocio|local)",
        re.I,
    ),
    "frecuencia": re.compile(
        r"puntual|recurrent|con qu[eé] frecuencia|cada cu[aá]nto|peri[oó]dic|de forma regular", re.I
    ),
    "producto": re.compile(
        r"qu[eé] presentaci[oó]n|cu[aá]l presentaci[oó]n|qu[eé] tama[ñn]o|cu[aá]l de", re.I
    ),
}
_MISSING = re.compile(
    r"\b(me falta|falta|necesito|dime|ind[ií]came|comp[aá]rteme|p[aá]same)\b", re.I
)
_AGAIN_AVAILABLE = re.compile(
    r"\bs[ií],?\s+(contamos|tenemos|manejamos|distribuimos)\b|\bs[ií],?\s+el\s+.{0,40}\s+est[aá]\s+disponible",
    re.I,
)
_ASKED_AVAILABILITY = re.compile(r"cuentan|tienen|distribuyen|manejan|disponible|venden", re.I)
# Reasons nobody gave it: the catalogue has no rotation, no «formato más fuerte», no «te conviene».
_INVENTED = re.compile(
    r"alta rotaci[oó]n|m[aá]s rota|rota m[aá]s|pensada para volumen|formato (m[aá]s )?fuerte|"
    r"te conviene(?! (definir|elegir|decidir|empezar|revisar|ver|pensar|aclarar))|le conviene|"
    r"ideal para (revender|negocio)|opci[oó]n destacada|m[aá]s (pedid[oa]|usad[oa]|vendid[oa])",
    re.I,
)


# A reply that hands the datum back like a receipt: «Anotado: *8 cajas*. ¿Para qué fecha...?», «Gracias,
# *Chorrillos*.». One is a confirmation; three in a conversation is a form (the owner's complaint:
# a person asks «¿y dónde queda tu hogar?», not «Anotado: *hogar*»). gpt-5.4 did it too: it was the
# instructions, not the model.
_RECEIPT = re.compile(
    r"^\W*(anotado|perfecto|gracias|listo|entendido|ok)\b[^\n*]{0,25}\*[^*\n]{1,60}\*|"
    r"\bya qued[oó] (anotad[oa]:? )?\*[^*\n]{1,60}\*",
    re.I,
)
RECEIPTS_ALLOWED = 2


def _label(call: dict[str, object]) -> str:
    """A tool call as the report shows it; a quote's data too, because that is what it decides."""
    base = f"{call.get('tool')}:{call.get('status')}"
    if call.get("tool") in ("anotar_cotizacion", "derivar_a_humano"):
        return f"{base} {str(call.get('args_resumen'))[:170]}"
    return base


def _plain(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def _asked(reply: str) -> set[str]:
    """The data a reply asks for."""
    found: set[str] = set()
    for sentence in re.split(r"(?<=[.?!])\s+|\n+", reply):
        if "?" not in sentence and not _MISSING.search(sentence):
            continue
        found.update(key for key, pattern in ASKS.items() if pattern.search(sentence))
    return found


# --------------------------------------------------------------------- scenarios


@dataclass(frozen=True)
class Line:
    text: str
    # What this message gives: `cantidad`, `fecha`, `distrito`, `producto`.
    gives: frozenset[str] = frozenset()
    # The customer asks about availability again in this message.
    asks_availability: bool = False


def say(text: str, *gives: str, asks_availability: bool = False) -> Line:
    return Line(text, frozenset(gives), asks_availability)


@dataclass(frozen=True)
class Scenario:
    name: str
    asks: tuple[str, ...]
    lines: tuple[Line, ...]
    # Words of what the customer gave that the message to the person must repeat back.
    recap: tuple[str, ...] = ()
    # When it must have gone to a person: after this many customer lines (None: no demand).
    handed_at: int | None = None
    # `water` is the simple catalogue (three presentations, one list of short data); `nayu` is the
    # real one (five products, questions of the owner's own).
    catalog: str = "water"


ALEXANDER = (
    "Hola buenos días.\nTe saluda Alexander, del área de logística de Control Sience\n"
    "Nos encontramos en la búsqueda de un proveedor para Agua en bidones: ustedes cuentan con el producto ?"
)
CF = ("cantidad", "fecha")
CFD = ("cantidad", "fecha", "distrito")

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "captura 1: cantidad y fecha juntas",
        CF,
        (
            say(ALEXANDER, asks_availability=True),
            say("5 bidones a la semana, lo queremos desde el día lunes", "cantidad", "fecha"),
        ),
        recap=("5", "lunes"),
        handed_at=2,
    ),
    Scenario(
        "captura 2: de a una",
        CF,
        (
            say("Hola, busco agua para revender"),
            say("de 20 L", "producto"),
            say("Quiero empezar con 15 bidones", "cantidad"),
            say("para el lunes", "fecha"),
        ),
        recap=("15", "lunes"),
        handed_at=4,
    ),
    Scenario(
        "captura 3: «de todas las presentaciones»",
        CF,
        (
            say("Buenos dias."),
            say(
                "Estaba llamando para consultar si distribuyen agua por mayor, quiero empezar a vender. ?",
                asks_availability=True,
            ),
            say("de todas las presentaciones", "producto"),
            say("unos 10 de cada una", "cantidad"),
            say("desde el lunes", "fecha"),
        ),
        recap=("10", "lunes"),
        handed_at=5,
    ),
    Scenario(
        "captura 4: «ya te dije que 20 semanales»",
        CF,
        (
            say("Hola, quiero agua por mayor, 20 bidones semanales", "cantidad"),
            say("de 20 L", "producto"),
            say("para negocio"),
            say("ya te dije que 20 semanales", "cantidad"),
            say("para el 26 de octubre", "fecha"),
        ),
        recap=("20", "26"),
        handed_at=5,
    ),
    Scenario(
        "captura 5: precio por mayor",
        CF,
        (
            say("Hola precio de agua"),
            say("Hola venta de agua. Precio por mayor"),
            say("20 semanales", "cantidad"),
            say("para negocio"),
            say("lo necesito desde el lunes", "fecha"),
        ),
        recap=("20", "lunes"),
        handed_at=5,
    ),
    Scenario(
        "captura 6: cantidad primero, fecha después",
        CF,
        (
            say(ALEXANDER.replace("\n", " "), asks_availability=True),
            say("quisieramos 5 semanales", "cantidad"),
            say("para el próximo lunes", "fecha"),
        ),
        recap=("5", "lunes"),
        handed_at=3,
    ),
    Scenario(
        "con dirección: cantidad, fecha y distrito",
        CFD,
        (
            say("Hola, necesito agua en bidones para mi oficina"),
            say("de 20 L, unos 8 por semana", "producto", "cantidad"),
            say("desde el lunes", "fecha"),
            say("en Miraflores, Av. Larco 123", "distrito"),
        ),
        recap=("8", "lunes", "Miraflores"),
        handed_at=4,
    ),
    Scenario(
        "con dirección: da todo en un mensaje",
        CFD,
        (
            say("Buenas tardes, ¿venden bidones de 20 litros?", asks_availability=True),
            say(
                "Necesitamos 12 por semana desde el lunes, entrega en San Isidro",
                "cantidad",
                "fecha",
                "distrito",
            ),
        ),
        recap=("12", "lunes", "San Isidro"),
        handed_at=2,
    ),
    Scenario(
        "sin datos configurados: cantidad y fecha",
        (),
        (
            say("Hola, quiero agua por mayor, 20 bidones semanales", "cantidad"),
            say("de 20 L", "producto"),
            say("para negocio"),
            say("ya te dije que 20 semanales", "cantidad"),
            say("para el 26 de octubre", "fecha"),
        ),
    ),
)


# Written after the first nine were fixed, and not tuned against: different customers (everything in
# one message, a plain refusal, a change of mind, one-word messages, an angry one) so that a fix that
# only learned the owner's six conversations shows up here.
HELD_OUT: tuple[Scenario, ...] = (
    Scenario(
        "nuevo: todo en el primer mensaje, con precio",
        CF,
        (
            say(
                "Hola, ¿cuánto cuesta el bidón de 20 litros? Necesito 30 por semana desde el 3 de noviembre",
                "cantidad",
                "fecha",
                asks_availability=True,
            ),
        ),
        recap=("30", "3"),
        handed_at=1,
    ),
    Scenario(
        "nuevo: dice que no sabe la fecha",
        CF,
        (
            say("Buenas, necesito bidones de 20 L", "producto"),
            say("unos 25 por semana", "cantidad"),
            say("todavía no sé desde cuándo, lo definimos luego"),
        ),
        recap=("25",),
        handed_at=3,
    ),
    Scenario(
        "nuevo: cambia de idea la cantidad",
        CF,
        (
            say("Hola, quiero cotizar agua en bidones", "producto"),
            say("5 por semana", "cantidad"),
            say("mejor que sean 10 por semana", "cantidad"),
            say("desde el miércoles", "fecha"),
        ),
        recap=("10", "miércoles"),
        handed_at=4,
    ),
    Scenario(
        "nuevo: fecha primero, cantidad después",
        CF,
        (
            say("Buenas tardes, ¿trabajan con agua de mesa por mayor?", asks_availability=True),
            say("lo necesito para el viernes", "fecha"),
            say("6 bidones", "cantidad"),
        ),
        recap=("6", "viernes"),
        handed_at=3,
    ),
    Scenario(
        "nuevo: mensajes de una palabra",
        CF,
        (
            say("hola"),
            say("bidones 20L", "producto"),
            say("10", "cantidad"),
            say("lunes", "fecha"),
        ),
        recap=("10", "lunes"),
        handed_at=4,
    ),
    Scenario(
        "nuevo: con dirección, el distrito primero",
        CFD,
        (
            say("Hola, soy de Surco y necesito agua en bidones", "distrito"),
            say("15 por semana", "cantidad"),
            say("desde el lunes", "fecha"),
        ),
        recap=("15", "lunes", "Surco"),
        handed_at=3,
    ),
    Scenario(
        "nuevo: cliente molesto",
        CF,
        (
            say("Buenos días, quisiera cotizar bidones de agua para mi restaurante", "producto"),
            say("12 por semana", "cantidad"),
            say("ya te dije que 12 por semana, ¿por qué preguntas lo mismo?", "cantidad"),
            say("para el lunes", "fecha"),
        ),
        recap=("12", "lunes"),
        handed_at=4,
    ),
    Scenario(
        "nuevo: pregunta algo en medio",
        CF,
        (
            say("Hola, busco agua de 7 litros para vender en mi bodega", "producto"),
            say("¿Tienen también de 500 ml?", asks_availability=True),
            say("ok, entonces 40 de 7 litros por semana", "cantidad"),
            say("desde la próxima semana", "fecha"),
        ),
        recap=("40",),
        handed_at=4,
    ),
    Scenario(
        "nuevo: cotiza sin datos configurados",
        (),
        (
            say("Hola, necesito agua por mayor para mi tienda", "producto"),
            say("unos 50 bidones al mes", "cantidad"),
            say("desde el 5 de noviembre", "fecha"),
        ),
    ),
)


def _nayu(name: str, lines: tuple[Line, ...], recap: tuple[str, ...], handed_at: int) -> Scenario:
    return Scenario(name, (), lines, recap=recap, handed_at=handed_at, catalog="nayu")


# On the catalogue as it is in production. The first is the owner's own conversation of 23 October
# (the assistant handed over with only the quantity, called the product by a name it does not have
# and recommended with reasons nobody gave); the rest are other customers, written after the fix.
NAYU: tuple[Scenario, ...] = (
    _nayu(
        "nayu 1: la conversación del dueño",
        (
            say("Buenos dias"),
            say(
                "estaba llamando para consultar si distribuyen agua por mayor, quiero empezar a vender",
                asks_availability=True,
            ),
            say("quiero el bidón de 20 L, unos 12 para empezar", "producto", "cantidad"),
            say("para chiclayo, la victoria", "distrito"),
            say("en tienda, quiero vender", "uso"),
            say("que precio tiene ?"),
            say("mejor 15 unidades", "cantidad"),
            say("para el lunes", "fecha"),
            say("serían pedidos recurrentes, todas las semanas", "frecuencia"),
        ),
        ("15", "lunes", "Victoria"),
        9,
    ),
    _nayu(
        "nayu 2: todo en un mensaje",
        (
            say(
                "Hola, necesito 20 bidones de 20 litros desde el lunes para mi tienda en La Victoria, "
                "Chiclayo, y compraríamos todas las semanas, pedidos recurrentes",
                "producto",
                "cantidad",
                "fecha",
                "uso",
                "distrito",
                "frecuencia",
            ),
        ),
        ("20", "lunes", "Victoria"),
        1,
    ),
    _nayu(
        "nayu 3: botellas de 625, no se pregunta la frecuencia",
        (
            say("Hola, quiero agua de mesa de 625 ml para mi gimnasio", "producto", "uso"),
            say("200 botellas", "cantidad"),
            say("para el viernes", "fecha"),
            say("en Surco", "distrito"),
        ),
        ("200", "viernes", "Surco"),
        4,
    ),
    _nayu(
        "nayu 4: no sabe la fecha",
        (
            say("Necesito bidones de 20 L para mi oficina", "producto", "uso"),
            say("unos 10", "cantidad"),
            say("en San Isidro", "distrito"),
            say("compra puntual, una sola vez", "frecuencia"),
            say("todavía no sé la fecha, lo veo luego"),
        ),
        ("10", "San Isidro"),
        5,
    ),
    _nayu(
        "nayu 5: pregunta el precio y cambia la cantidad",
        (
            say("Hola, ¿precio del bidón de 20 L?", "producto", asks_availability=True),
            say("¿y cuánto cuesta por mayor?"),
            say("unos 30 al mes", "cantidad"),
            say("mejor 40", "cantidad"),
            say("para el 3 de noviembre", "fecha"),
            say("Miraflores, Av. Larco 123", "distrito"),
            say("para mi restaurante", "uso"),
            say("puntual", "frecuencia"),
        ),
        ("40", "noviembre", "Miraflores"),
        8,
    ),
    _nayu(
        "nayu 6: agua alcalina",
        (
            say("Hola, ¿venden agua alcalina?", "producto", asks_availability=True),
            say("50 botellas", "cantidad"),
            say("para el martes", "fecha"),
            say("Av. Benavides 500, Miraflores", "distrito"),
            say("para mi gimnasio", "uso"),
            say("pedidos recurrentes", "frecuencia"),
        ),
        ("50", "martes", "Miraflores"),
        6,
    ),
    _nayu(
        "nayu 7: molesto porque le repiten una pregunta",
        (
            say("Quiero cotizar la caja de agua de 20 L", "producto"),
            say("8 cajas", "cantidad"),
            say("ya te dije 8 cajas, ¿por qué preguntas lo mismo?", "cantidad"),
            say("para mi hogar", "uso"),
            say("el lunes", "fecha"),
            say("Chorrillos", "distrito"),
            say("una sola vez", "frecuencia"),
        ),
        ("8", "lunes", "Chorrillos"),
        7,
    ),
    _nayu(
        "nayu 8: respuestas de una palabra",
        (
            say("hola"),
            say("bidón 20L", "producto"),
            say("10", "cantidad"),
            say("lunes", "fecha"),
            say("Surco", "distrito"),
            say("tienda", "uso"),
            say("semanal", "frecuencia"),
        ),
        ("10", "lunes", "Surco"),
        7,
    ),
)

ALL_SCENARIOS = SCENARIOS + HELD_OUT + NAYU


# ----------------------------------------------------------------------- results


@dataclass
class Outcome:
    scenario: Scenario
    transcript: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    judge: dict[str, object] | None = None
    # What the model was asked and wrote, measured, so a run says what it cost.
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


def _check_turn(
    outcome: Outcome,
    index: int,
    line: Line,
    given_before: set[str],
    reply: str,
    *,
    availability_said: bool,
) -> None:
    """The per-turn defects, against what had been given by the end of this customer line."""
    given = given_before | set(line.gives)
    again = _asked(reply) & given
    # A presentation named in this very line is not asked again, but «de todas» still counts.
    if again:
        outcome.failures.append(
            f"turno {index}: vuelve a pedir {sorted(again)} que ya dio: «{reply[:90]}»"
        )
    if availability_said and not line.asks_availability and _AGAIN_AVAILABLE.search(reply):
        outcome.failures.append(f"turno {index}: vuelve a decir que lo tiene: «{reply[:90]}»")
    if _INVENTED.search(reply):
        outcome.failures.append(f"turno {index}: argumento que nadie le dio: «{reply[:90]}»")


async def run_scenario(session: Session, scenario: Scenario, *, llm: LLMProvider) -> Outcome:
    tenant = seed(session, nayu() if scenario.catalog == "nayu" else water(scenario.asks))
    contact = Contact(tenant_id=tenant.id, external_user_id=f"sandbox:{uuid.uuid4()}")
    session.add(contact)
    session.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="sandbox", contact_id=contact.id, status="open"
    )
    session.add(conversation)
    session.flush()

    outcome = Outcome(scenario)
    receipts = 0
    given: set[str] = set()
    availability_said = False
    handed_at: int | None = None
    last_reply = ""
    for index, line in enumerate(scenario.lines, start=1):
        session.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content=line.text,
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
        result = await pipeline.run(conversation.id, InboundTurn(text=line.text))
        reply = "\n".join(message.content for message in result.messages)
        calls = result.reply.tools_called if result.reply else []
        if result.reply:
            outcome.tokens_in += result.reply.input_tokens
            outcome.tokens_out += result.reply.output_tokens
        outcome.transcript.append(f"- Cliente: {line.text}")
        outcome.transcript.append(
            f"  - {result.stage_before.value} → {result.stage_after.value} · "
            f"{[_label(c) for c in calls]}"
        )
        outcome.transcript.extend(f"  - Nayu: {message.content}" for message in result.messages)
        handed = result.stage_after is Stage.DERIVADO
        if reply:
            _check_turn(outcome, index, line, given, reply, availability_said=availability_said)
            receipts += bool(_RECEIPT.search(reply))
        given |= set(line.gives)
        if _AGAIN_AVAILABLE.search(reply) or re.search(r"se cotiza", reply, re.I):
            availability_said = True
        last_reply = reply
        if handed:
            handed_at = index
            break

    if receipts > RECEIPTS_ALLOWED:
        outcome.failures.append(
            f"suena a formulario: {receipts} respuestas devuelven el dato como un recibo"
        )
    if scenario.handed_at is not None:
        if handed_at is None:
            outcome.failures.append("no pasó a una persona aunque ya tenía todos los datos")
        elif handed_at < scenario.handed_at:
            outcome.failures.append(
                f"pasó a una persona antes de tener los datos (turno {handed_at})"
            )
        elif handed_at > scenario.handed_at:
            outcome.failures.append(
                f"pasó a una persona tarde (turno {handed_at}, debía ser el {scenario.handed_at})"
            )
        elif scenario.recap:
            missing = [word for word in scenario.recap if _plain(word) not in _plain(last_reply)]
            if missing:
                outcome.failures.append(
                    f"el aviso final no le devuelve lo que dijo ({missing}): «{last_reply[:90]}»"
                )
    return outcome


# ------------------------------------------------------------------------ judge

JUDGE_PROMPT = """\
Eres una persona exigente que revisa conversaciones de un asistente de WhatsApp de una distribuidora de agua
que cotiza por mayor (el precio lo da una persona; el asistente solo junta datos y pasa la cotización).
Lee la conversación completa como la leería el cliente. Responde SOLO un JSON:
{"razonable": true|false, "fallas": ["..."], "mejor_respuesta_del_primer_error": "..."}
Es NO razonable si ocurre cualquiera de estas: devuelve cada dato como un recibo ("Anotado: *8 cajas*") en vez de
conversar; recibe una respuesta vaga o que no encaja ("mi hogar" cuando pidió la dirección) y sigue con otra
pregunta sin preguntar lo necesario para entenderla; pregunta algo que el cliente ya dijo; repite la misma pregunta;
ignora una corrección del cliente; dice otra vez que tiene el producto cuando nadie lo preguntó; da razones de
venta que no vienen del negocio; responde sin avanzar hacia pasar la cotización; o suena robótico y repetitivo.
Una breve confirmación de un dato nuevo ("Perfecto, 15 bidones") es normal y razonable.
"""


async def judge(outcome: Outcome, *, llm: LLMProvider) -> dict[str, object]:
    transcript = "\n".join(outcome.transcript)
    result = await llm.chat(
        [ChatMessage("system", JUDGE_PROMPT), ChatMessage("user", transcript)],
        temperature=0.0,
        max_tokens=500,
    )
    text = (result.content or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group(0)) if match else {"razonable": None, "fallas": [text[:200]]}
    except json.JSONDecodeError:
        return {"razonable": None, "fallas": [text[:200]]}


@dataclass
class Report:
    outcomes: list[Outcome]

    @property
    def ratio(self) -> float:
        return sum(o.ok for o in self.outcomes) / len(self.outcomes) if self.outcomes else 0.0

    @property
    def ok(self) -> bool:
        return self.ratio >= PASS_RATIO

    def render(self) -> str:
        lines = [
            f"# Conversaciones reales: {sum(o.ok for o in self.outcomes)}/{len(self.outcomes)} "
            f"sin fallas ({self.ratio:.0%}, se pide {PASS_RATIO:.0%})",
            f"tokens del agente: {sum(o.tokens_in for o in self.outcomes)} de entrada, "
            f"{sum(o.tokens_out for o in self.outcomes)} de salida",
        ]
        for outcome in self.outcomes:
            lines.append(f"\n### {'✅' if outcome.ok else '❌'} {outcome.scenario.name}")
            lines.extend(outcome.transcript)
            lines.extend(f"  - ⚠ {failure}" for failure in outcome.failures)
            if outcome.judge is not None:
                lines.append(f"  - juez: {json.dumps(outcome.judge, ensure_ascii=False)}")
        return "\n".join(lines)


async def run_all(
    session: Session,
    scenarios: Sequence[Scenario],
    *,
    llm: LLMProvider,
    judged: bool = False,
) -> Report:
    outcomes = []
    for scenario in scenarios:
        outcome = await run_scenario(session, scenario, llm=llm)
        if judged:
            outcome.judge = await judge(outcome, llm=llm)
        outcomes.append(outcome)
    return Report(outcomes)
