"""Scripted sales conversations, one business per template (Feature 004, TASK-447).

Eight conversations per template (24 in all), each a few customer messages run
through the whole turn pipeline in the simulator with the real model. Every case
checks, on every turn:

* at most 3 outbound messages (C-11);
* no library file sent twice;
* never "confirmed" before an operator (no operator acts in these runs);
* every amount the assistant writes in soles is a real one: a catalogue price, a
  delivery cost, or a subtotal, shipping or total of the order as it stands;

and, at the end, the stage it should have reached and the tools it must have used.
A template passes with 90 % of its cases.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn, TurnPipeline, TurnResult
from app.models.business import AgentConfig, BusinessProfile, PaymentMethodBusiness
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.sales_config import Promotion
from app.models.tenant import Tenant
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.providers.llm.base import LLMProvider
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.services.turn_outbound import SandboxOutbound

MAX_OUTBOUND = 3
PASS_RATIO = 0.9
# The agenda has four cases and the model varies: three of four is a pass (measured over 20 runs).
PASS_RATIOS = {"agenda": 0.75}
_AMOUNT = re.compile(r"S/\.?\s?(\d[\d.,]*)")
_CONFIRMED = re.compile(
    r"\b(pago|pedido|compra)\s+(ya\s+)?(ha\s+sido\s+|fue\s+|est[aá]\s+|qued[oó]\s+)?confirmad[oa]\b"
    r"|\bconfirmamos\s+(tu|su)\s+pago\b",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ businesses


@dataclass(frozen=True)
class Business:
    name: str
    industry: str
    template: str
    description: str
    # (name, price, category, modo_venta, bundle_size, es_digital, destacado)
    products: tuple[tuple[str, str, str, str, int | None, bool, bool], ...]
    payment: str
    deliveries: dict[str, Any] | None = None
    promotion: str | None = None
    # Extra setup of a business that does more than sell (the agenda: hours, capabilities).
    setup: Callable[[Session, Tenant], None] | None = None


SHOP = Business(
    name="Tienda Demo",
    industry="retail",
    template="productos",
    description="Tienda de ropa y accesorios urbanos en Lima.",
    products=(
        ("Polo básico", "35.00", "Ropa", "precio_fijo", None, False, True),
        ("Gorra urbana", "25.00", "Ropa", "precio_fijo", None, False, False),
        ("Mochila ejecutiva", "120.00", "Accesorios", "precio_fijo", None, False, False),
    ),
    payment="Yape al 912345678 a nombre de Tienda Demo.",
    deliveries={
        "hace_delivery": True,
        "zonas": [
            {"nombre": "Miraflores", "costo": "8.00", "tiempo": "24 h"},
            {"nombre": "San Isidro", "costo": "8.00", "tiempo": "24 h"},
            {"nombre": "Callao", "costo": "12.00", "tiempo": "24 a 48 h"},
        ],
        "envio_gratis_desde": "150.00",
        "recojo_en_tienda": False,
    },
    promotion="2x1 en polos",
)
SCHOOL = Business(
    name="Escuela Capacita",
    industry="instituto",
    template="certificaciones",
    description="Certificaciones de experiencia para el sector público.",
    products=(
        (
            "Certificación individual SIAF",
            "120.00",
            "Sector Público",
            "precio_fijo",
            None,
            True,
            False,
        ),
        (
            "Certificación individual Gestión pública",
            "120.00",
            "Sector Público",
            "precio_fijo",
            None,
            True,
            False,
        ),
        (
            "Certificación individual Excel avanzado",
            "120.00",
            "Ofimática",
            "precio_fijo",
            None,
            True,
            False,
        ),
        (
            "Certificación individual Power BI",
            "120.00",
            "Ofimática",
            "precio_fijo",
            None,
            True,
            False,
        ),
        ("Paquete 2 certificaciones", "200.00", "Paquetes", "precio_fijo", 2, True, False),
        ("Paquete 5 certificaciones", "500.00", "Paquetes", "precio_fijo", 5, True, True),
    ),
    payment="Yape al 987654321 a nombre de Escuela Capacita.",
)
AGENCY = Business(
    name="Zentek360",
    industry="servicios",
    template="servicios",
    description="Agencia digital: páginas web, hosting y mantenimiento.",
    products=(
        ("Hosting anual", "350.00", "Hosting", "precio_fijo", None, True, True),
        ("Mantenimiento web mensual", "150.00", "Mantenimiento", "precio_fijo", None, True, False),
        ("Desarrollo de página web", "0.00", "Desarrollo", "cotizacion", None, True, False),
    ),
    payment="Transferencia BCP 191-1234567-0-12 a nombre de Zentek360 SAC.",
)


def _open_the_agenda(session: Session, tenant: Tenant) -> None:
    """A dental clinic that books: capabilities, Monday to Friday hours, two bookable services."""
    config = session.query(AgentConfig).filter(AgentConfig.tenant_id == tenant.id).one()
    config.capacidades = ["atender", "agendar"]
    profile = session.query(BusinessProfile).filter(BusinessProfile.tenant_id == tenant.id).one()
    profile.timezone = "America/Lima"
    profile.opening_hours = {
        day: [["09:00", "13:00"], ["15:00", "18:00"]] for day in ("mon", "tue", "wed", "thu", "fri")
    }
    for product in ProductRepository(session, tenant.id).list():
        product.agendable = True
        product.duracion_min = 45 if "Consulta" in product.name else 60


CLINIC = Business(
    name="Clínica Sonrisa",
    industry="salud",
    template="agenda",
    description="Clínica dental en Miraflores: consultas, limpiezas y ortodoncia.",
    products=(
        ("Consulta dental", "80.00", "Consultas", "precio_fijo", None, False, True),
        ("Limpieza dental", "120.00", "Tratamientos", "precio_fijo", None, False, False),
    ),
    payment="Pago en la clínica, en efectivo o con tarjeta.",
    setup=_open_the_agenda,
)


def _qualify_leads(session: Session, tenant: Tenant) -> None:
    """A marketing agency that quotes and qualifies: the questions are its rubro's own."""
    config = session.query(AgentConfig).filter(AgentConfig.tenant_id == tenant.id).one()
    config.capacidades = ["atender", "cotizar", "captar"]


STUDIO = Business(
    name="Agencia Pixel",
    industry="servicios",
    template="captacion",
    description="Agencia peruana de marketing digital: páginas web, redes sociales y publicidad.",
    products=(
        ("Página web corporativa", "1500.00", "Web", "cotizacion", None, True, True),
        ("Gestión de redes sociales", "600.00", "Marketing", "cotizacion", None, True, False),
    ),
    payment="Transferencia BCP a nombre de Agencia Pixel SAC.",
    setup=_qualify_leads,
)
BUSINESSES = {b.template: b for b in (SHOP, SCHOOL, AGENCY, CLINIC, STUDIO)}


def seed(session: Session, business: Business) -> Tenant:
    tenant = Tenant(
        name=f"{business.name} eval {uuid.uuid4().hex[:6]}",
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=14),
    )
    session.add(tenant)
    session.flush()
    session.add_all(
        [
            BusinessProfile(
                tenant_id=tenant.id,
                business_name=business.name,
                industry=business.industry,
                description=business.description,
                currency="PEN",
            ),
            AgentConfig(
                tenant_id=tenant.id,
                agent_name="Leo",
                is_active=True,
                # The clinic and the studio run the services template; the key only labels flows.
                process_template_key=(
                    "servicios"
                    if business.template in ("agenda", "captacion")
                    else business.template
                ),
            ),
            PaymentMethodBusiness(
                tenant_id=tenant.id,
                method="yape" if "Yape" in business.payment else "transferencia",
                instructions=business.payment,
                titular=business.name,
            ),
        ]
    )
    session.flush()
    products = ProductRepository(session, tenant.id)
    added = {}
    for name, price, category, mode, size, digital, featured in business.products:
        added[name] = products.add(
            name=name,
            price=Decimal(price),
            category=category,
            modo_venta=mode,
            bundle_size=size,
            bundle_permite_libres=size is not None,
            es_digital=digital,
            destacado=featured,
        )
    if business.deliveries:
        BusinessInfoRepository(session, tenant.id).upsert("entregas", dict(business.deliveries))
    if business.promotion:
        polo = added["Polo básico"]
        session.add(
            Promotion(
                tenant_id=tenant.id,
                nombre=business.promotion,
                descripcion="Llevas 2 polos y pagas 1",
                uso="automatica",
                tipo="nxm",
                n=2,
                m=1,
                aplica_a="productos",
                product_ids=[polo.id],
            )
        )
    session.flush()
    if business.setup is not None:
        business.setup(session, tenant)
        session.flush()
    return tenant


def known_amounts(business: Business) -> set[Decimal]:
    amounts = {Decimal(p[1]) for p in business.products if Decimal(p[1]) > 0}
    for zone in (business.deliveries or {}).get("zonas", []):
        amounts.add(Decimal(zone["costo"]))
    if business.deliveries and business.deliveries.get("envio_gratis_desde"):
        amounts.add(Decimal(business.deliveries["envio_gratis_desde"]))
    return amounts


# ------------------------------------------------------------------ the cases


@dataclass(frozen=True)
class FlowCase:
    template: str
    name: str
    turns: tuple[str, ...]
    final_stages: frozenset[str]
    tools: frozenset[str] = frozenset()
    # Words that must appear in some reply (case-insensitive), any of them.
    mentions_any: tuple[str, ...] = ()
    # Words no reply may contain.
    never: tuple[str, ...] = ()


def _case(
    template: str,
    name: str,
    turns: Sequence[str],
    stages: Sequence[str],
    tools: Sequence[str] = (),
    mentions_any: Sequence[str] = (),
    never: Sequence[str] = (),
) -> FlowCase:
    return FlowCase(
        template,
        name,
        tuple(turns),
        frozenset(stages),
        frozenset(tools),
        tuple(mentions_any),
        tuple(never),
    )


SHOP_DATA = (
    "Me llamo Ana Torres, mi celular es 987654321. Delivery a Miraflores, Av. Larco 123, "
    "referencia frente al parque."
)
SCHOOL_DATA = (
    "Me llamo Luis Pérez Rojas, DNI 45678912, correo luis.perez@mail.com, celular 976543210"
)
AGENCY_DATA = (
    "Soy Carla Díaz, de la empresa Díaz Constructores SAC, RUC 20123456789, "
    "correo carla@empresa.pe, celular 912345678"
)

FLOW_CASES: tuple[FlowCase, ...] = (
    # ---------------------------------------------------------------- productos
    _case(
        "productos",
        "pregunta un precio",
        ["Hola", "¿Cuánto cuesta la mochila ejecutiva?"],
        ["IDENTIFICAR"],
        ["buscar_productos"],
        mentions_any=["120"],
    ),
    _case(
        "productos",
        "agrega una gorra",
        ["Hola, quiero una gorra urbana", "Sí, agrégala. Solo una"],
        ["PEDIDO", "DATOS", "COBRO"],
        ["gestionar_pedido"],
    ),
    _case(
        "productos",
        "pedido completo hasta el cobro",
        ["Hola, quiero la mochila ejecutiva", "Sí, agrégala", SHOP_DATA],
        ["COBRO"],
        ["gestionar_pedido", "registrar_datos_cliente"],
    ),
    _case(
        "productos",
        "pregunta por promociones",
        ["Hola, ¿tienen alguna promoción?"],
        ["IDENTIFICAR", "INICIO"],
        mentions_any=["2x1", "2 polos", "dos polos"],
    ),
    _case(
        "productos",
        "pide una persona",
        ["Hola", "Quiero hablar con una persona, por favor"],
        ["DERIVADO"],
        ["derivar_a_humano"],
    ),
    _case(
        "productos",
        "producto que no existe",
        ["Hola, ¿venden zapatillas?"],
        ["IDENTIFICAR", "INICIO"],
        ["buscar_productos"],
        never=["zapatillas a S/"],
    ),
    _case(
        "productos",
        "zona sin delivery",
        [
            "Quiero un polo básico",
            "Sí, uno",
            "Mándalo a Arequipa, a nombre de Ana Torres, celular 987654321",
        ],
        ["PEDIDO", "DATOS"],
        ["gestionar_pedido"],
        mentions_any=["Miraflores", "San Isidro", "Callao"],
    ),
    _case(
        "productos",
        "pide cómo pagar",
        ["Quiero una gorra urbana", "Sí, agrégala", SHOP_DATA, "¿Cómo pago?"],
        ["COBRO"],
        ["enviar_metodos_pago"],
    ),
    # ---------------------------------------------------------- certificaciones
    _case(
        "certificaciones",
        "saluda y le ofrecen caminos",
        ["Hola, buenas tardes"],
        ["CALIFICAR", "IDENTIFICAR"],
    ),
    _case(
        "certificaciones",
        "certifica SIAF y pregunta el precio",
        ["Hola", "Soy Luis, solo quiero certificar mi experiencia en SIAF", "¿Cuánto cuesta?"],
        ["IDENTIFICAR", "PEDIDO", "DATOS"],
        ["buscar_productos"],
        mentions_any=["120"],
    ),
    _case(
        "certificaciones",
        "paquete de 5 con 2 temas",
        [
            "Hola, soy Luis. Solo quiero certificar mi experiencia",
            "Quiero el paquete de 5 certificaciones",
            "Por ahora SIAF y Excel avanzado",
        ],
        ["PEDIDO"],
        ["gestionar_pedido"],
        mentions_any=["3"],
    ),
    _case(
        "certificaciones",
        "programa completo se deriva",
        [
            "Hola",
            "Quiero llevar un programa completo",
            "Me interesa Gestión pública, puedo en las noches",
        ],
        ["DERIVADO", "ASESORIA"],
        ["derivar_a_humano"],
    ),
    _case(
        "certificaciones",
        "lo pensaré",
        [
            "Hola, soy Luis. Solo quiero certificar",
            "¿Cuánto cuesta la de Power BI?",
            "Lo voy a pensar",
        ],
        ["IDENTIFICAR", "PEDIDO"],
        never=["descuento del", "% de descuento"],
    ),
    _case(
        "certificaciones",
        "individual hasta el cobro",
        [
            "Hola, soy Luis. Solo quiero certificar",
            "Quiero la certificación de Power BI",
            "Sí, agrégala",
            SCHOOL_DATA,
        ],
        ["COBRO", "DATOS"],
        ["gestionar_pedido", "registrar_datos_cliente"],
    ),
    _case(
        "certificaciones",
        "pregunta algo que el negocio no dijo",
        [
            "Hola, soy Luis. Solo quiero certificar",
            "¿Las certificaciones están reconocidas por SERVIR?",
        ],
        ["IDENTIFICAR", "DERIVADO", "CALIFICAR"],
        never=["sí, están reconocidas por servir"],
    ),
    _case(
        "certificaciones",
        "pide los métodos de pago",
        [
            "Hola, soy Luis. Solo quiero certificar",
            "Quiero la certificación individual de SIAF",
            "Sí, agrégala",
            SCHOOL_DATA,
            "¿A dónde pago?",
        ],
        ["COBRO"],
        ["enviar_metodos_pago"],
    ),
    # ---------------------------------------------------------------- servicios
    _case(
        "servicios",
        "pregunta qué hacen",
        ["Hola, ¿qué servicios tienen?"],
        ["IDENTIFICAR", "INICIO", "CALIFICAR"],
        mentions_any=["Hosting", "Mantenimiento"],
    ),
    _case(
        "servicios",
        "una web se cotiza con una persona",
        [
            "Hola",
            "Quiero una página web para mi restaurante, con carta y reservas",
            # v3 asks for a name and an email before handing a quote to an advisor.
            "Soy Carla Díaz, carla@empresa.pe",
            # It may ask for the company before handing over; either way it ends handed over.
            "Mi empresa es Restaurante Díaz SAC",
        ],
        ["DERIVADO"],
        ["derivar_a_humano"],
    ),
    _case(
        "servicios",
        "hosting de precio fijo",
        ["Hola, quiero contratar el hosting anual", "Sí, agrégalo"],
        ["PEDIDO", "DATOS", "COBRO"],
        ["gestionar_pedido"],
        mentions_any=["350"],
    ),
    _case(
        "servicios",
        "hosting hasta el cobro",
        ["Hola, quiero el hosting anual", "Sí, agrégalo", AGENCY_DATA],
        ["COBRO", "DATOS"],
        ["gestionar_pedido", "registrar_datos_cliente"],
    ),
    _case(
        "servicios",
        "pide un asesor",
        ["Hola", "Prefiero hablar con un asesor"],
        ["DERIVADO"],
        ["derivar_a_humano"],
    ),
    _case(
        "servicios",
        "precio de una web no se inventa",
        ["Hola, ¿cuánto cuesta una página web?"],
        ["IDENTIFICAR", "DERIVADO", "CALIFICAR", "INICIO"],
    ),
    _case(
        "servicios",
        "precio del mantenimiento",
        ["Hola, ¿cuánto cuesta el mantenimiento mensual?"],
        ["IDENTIFICAR", "INICIO", "CALIFICAR"],
        ["buscar_productos"],
        mentions_any=["150"],
    ),
    _case(
        "servicios",
        "pide cómo pagar el hosting",
        ["Hola, quiero el hosting anual", "Sí, agrégalo", AGENCY_DATA, "¿Cómo pago?"],
        ["COBRO"],
        ["enviar_metodos_pago"],
    ),
    # ------------------------------------------------------------------- agenda
    _case(
        "agenda",
        "pide ver los horarios",
        ["Hola, quiero agendar una consulta dental", "Para esta semana, ¿qué horarios tienen?"],
        ["INICIO", "IDENTIFICAR", "ASESORIA", "PEDIDO", "DATOS"],
        ["consultar_horarios"],
        mentions_any=[":00", ":30"],
    ),
    _case(
        "agenda",
        "agenda la cita que eligió",
        [
            "Hola, quiero agendar una limpieza dental",
            "¿Qué horarios hay disponibles?",
            "Me sirve el primero que me diste. Soy María Pérez",
        ],
        ["INICIO", "IDENTIFICAR", "ASESORIA", "PEDIDO", "DATOS", "CERRADO"],
        ["consultar_horarios", "agendar_cita"],
    ),
    _case(
        "agenda",
        "no agenda para dentro de minutos",
        ["Hola, necesito una consulta dental ahora mismo, en 15 minutos"],
        ["INICIO", "IDENTIFICAR", "ASESORIA", "PEDIDO", "DATOS"],
        never=["ya quedó agendada", "ya está agendada", "te espero en 15 minutos"],
    ),
    _case(
        "agenda",
        "no inventa un horario fuera de la agenda",
        ["Hola, ¿atienden los domingos a las 8 de la noche? Quiero una consulta dental"],
        ["INICIO", "IDENTIFICAR", "ASESORIA", "PEDIDO", "DATOS"],
        never=["sí atendemos los domingos", "claro, el domingo a las 8"],
    ),
    # ----------------------------------------------------------------- captación
    _case(
        "captacion",
        "un cliente con urgencia se califica",
        [
            "Hola, necesito una página web para mi clínica para el mes que viene, "
            "¿me pasan una propuesta?"
        ],
        ["INICIO", "IDENTIFICAR", "ASESORIA", "PEDIDO", "DATOS", "DERIVADO"],
        ["calificar_cliente"],
    ),
    _case(
        "captacion",
        "quien solo mira recibe una pregunta, no un interrogatorio",
        ["Hola, quería saber qué hacen"],
        ["INICIO", "IDENTIFICAR", "ASESORIA"],
        mentions_any=["?"],
        never=["dni", "tarjeta", "número de cuenta"],
    ),
    _case(
        "captacion",
        "no pide datos sensibles al conocerlo",
        ["Hola, quiero cotizar redes sociales para mi tienda, mi plazo es de dos meses"],
        ["INICIO", "IDENTIFICAR", "ASESORIA", "DERIVADO"],
        never=["dni", "tarjeta", "número de cuenta", "contraseña"],
    ),
)


# ------------------------------------------------------------------ the runner


@dataclass
class FlowOutcome:
    case: FlowCase
    failures: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    final_stage: str = ""

    @property
    def ok(self) -> bool:
        return not self.failures


def _tool_label(call: dict[str, Any]) -> str:
    """`tool:status`, so a transcript shows whether a tool did its job or refused."""
    label = f"{call.get('tool')}:{call.get('status')}"
    if call.get("tool") in ("agendar_cita", "consultar_horarios", "gestionar_cita"):
        label += f" {str(call.get('args_resumen'))[:110]}"
    return label


def _amounts(text: str) -> set[Decimal]:
    """Every sol amount in a text: "S/ 1200", "S/ 1,200.00" and "S/ 110.5" alike."""
    found: set[Decimal] = set()
    for raw in _AMOUNT.findall(text):
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


def _order_amounts(result: TurnResult) -> set[Decimal]:
    pedido = result.facts.pedido
    if pedido is None:
        return set()
    values = set()
    for name in ("total", "subtotal", "envio", "descuento"):
        value = getattr(pedido, name, None)
        if value is not None:
            values.add(Decimal(str(value)).quantize(Decimal("0.01")))
    return values


async def run_case(session: Session, case: FlowCase, *, llm: LLMProvider) -> FlowOutcome:
    business = BUSINESSES[case.template]
    tenant = seed(session, business)
    contact = Contact(tenant_id=tenant.id, external_user_id=f"sandbox:{uuid.uuid4()}")
    session.add(contact)
    session.flush()
    conversation = Conversation(
        tenant_id=tenant.id, channel="sandbox", contact_id=contact.id, status="open"
    )
    session.add(conversation)
    session.flush()

    outcome = FlowOutcome(case)
    allowed = {amount.quantize(Decimal("0.01")) for amount in known_amounts(business)}
    allowed.add(Decimal("0.00"))  # no shipping, no discount
    tools: set[str] = set()
    sent_resources: list[str] = []
    replies: list[str] = []
    result: TurnResult | None = None
    for text in case.turns:
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
        allowed |= _order_amounts(result)
        said = [m.content for m in result.messages]
        replies.extend(said)
        # What the customer reads in a list or on the buttons counts as said.
        replies.extend(
            json.dumps(m.payload, ensure_ascii=False) for m in result.messages if m.payload
        )
        calls = result.reply.tools_called if result.reply else []
        for call in calls:
            tools.add(str(call.get("tool")))
            if call.get("tool") == "enviar_recurso" and call.get("status") == "hecho":
                sent_resources.append(str(call.get("args_resumen")))
        outcome.transcript.append(f"- Cliente: {text}")
        outcome.transcript.append(
            f"  - {result.stage_before.value} → {result.stage_after.value} · tools "
            f"{[_tool_label(c) for c in calls]}"
        )
        outcome.transcript.extend(f"  - Leo: {line}" for line in said)

        if len(result.messages) > MAX_OUTBOUND:
            outcome.failures.append(f"{len(result.messages)} mensajes en un turno")
        for line in said:
            if _CONFIRMED.search(line):
                outcome.failures.append(f"dijo confirmado sin operador: {line[:80]}")
            invented = _amounts(line) - allowed
            if invented:
                outcome.failures.append(
                    f"montos que no existen {sorted(str(a) for a in invented)}: {line[:80]}"
                )

    assert result is not None
    outcome.final_stage = result.stage_after.value
    if outcome.final_stage not in case.final_stages:
        outcome.failures.append(
            f"terminó en {outcome.final_stage}, se esperaba {sorted(case.final_stages)}"
        )
    missing = case.tools - tools
    if missing:
        outcome.failures.append(f"no usó {sorted(missing)}")
    if len(sent_resources) != len(set(sent_resources)):
        outcome.failures.append(f"recurso repetido: {sent_resources}")
    joined = " ".join(replies).lower()
    if case.mentions_any and not any(word.lower() in joined for word in case.mentions_any):
        outcome.failures.append(f"no mencionó ninguno de {list(case.mentions_any)}")
    for word in case.never:
        if word.lower() in joined:
            outcome.failures.append(f"dijo «{word}»")
    return outcome


@dataclass
class FlowReport:
    outcomes: list[FlowOutcome]

    def ratio(self, template: str) -> float:
        mine = [o for o in self.outcomes if o.case.template == template]
        return sum(o.ok for o in mine) / len(mine) if mine else 0.0

    @property
    def ok(self) -> bool:
        run = {o.case.template for o in self.outcomes}
        return all(self.ratio(t) >= PASS_RATIOS.get(t, PASS_RATIO) for t in run)

    def render(self) -> str:
        lines = ["# Evals de flujos de venta (TASK-447)", ""]
        for template in BUSINESSES:
            mine = [o for o in self.outcomes if o.case.template == template]
            lines.append(
                f"## {template}: {sum(o.ok for o in mine)}/{len(mine)} ({self.ratio(template):.0%})"
            )
            lines.append("")
            for o in mine:
                mark = "✅" if o.ok else "❌"
                lines.append(f"### {mark} {o.case.name} (final {o.final_stage})")
                lines.extend(f"- **Falla:** {f}" for f in o.failures)
                lines.extend(o.transcript)
                lines.append("")
        return "\n".join(lines)


async def run_flows(session: Session, cases: Sequence[FlowCase], *, llm: LLMProvider) -> FlowReport:
    outcomes = []
    for case in cases:
        savepoint = session.begin_nested()
        try:
            outcomes.append(await run_case(session, case, llm=llm))
        finally:
            savepoint.rollback()
    return FlowReport(outcomes)
