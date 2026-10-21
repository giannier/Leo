"""How far a business got configuring its assistant (Feature 004 §1, TASK-437).

Two lists for the panel's home: the state of each section, and the steps in order with the next
one to do. The steps are **one list for the whole setup** (feature 019) and depend on what the agent
does: an agent that sells asks for a catalogue and a payment method, one that quotes asks for a
catalogue and a contact for the cases that need a person, one that only answers needs neither.

Progress: a required step weighs 2 and a recommended one 1; progress is the share of the weight.

"Probaste el simulador" is the first sandbox conversation that reached
IDENTIFICAR or a later stage; "probaste una situación" is a sandbox turn whose
trace has situations. Both are read from what happened, so resetting the
simulator (which deletes its conversations) makes them pending again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.models.knowledge import KnowledgeSource
from app.models.media import MediaAsset
from app.models.sales_config import Faq, Promotion
from app.models.situation import Situation
from app.process.capabilities import DEFAULT_CAPABILITIES, leads_to_a_sale, sells
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.payment import PaymentMethodRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.repositories.whatsapp import WhatsAppNumberRepository

SectionState = Literal["completa", "parcial", "vacia", "opcional"]
PAST_IDENTIFICAR = (
    "IDENTIFICAR",
    "PEDIDO",
    "DATOS",
    "COBRO",
    "VERIFICACION",
    "CERRADO",
    "ASESORIA",
)
STEP_ACTIONS = {
    "negocio": "/onboarding?paso=1",
    "agente": "/onboarding?paso=2",
    "productos": "/catalog",
    "metodos_pago": "/asistente/informacion",
    "conocimiento": "/knowledge",
    "informacion": "/asistente/informacion",
    "contacto": "/asistente/informacion?bloque=contacto",
    "primera_situacion": "/asistente/situaciones",
    "probar_simulador": "/sandbox",
    "probar_situacion": "/asistente/situaciones",
    "conectar_whatsapp": "/channel",
}
# Where a step belongs in the story of the setup.
GROUPS = ("probar", "clientes", "mejorar")


@dataclass(frozen=True)
class Section:
    clave: str
    estado: SectionState
    detalle: dict[str, SectionState] = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    paso: str
    hecho: bool
    accion: str
    # "probar" (to try the agent), "clientes" (to serve real customers) or "mejorar".
    grupo: str = "probar"
    requerido: bool = True


@dataclass(frozen=True)
class Overview:
    progreso: int
    secciones: list[Section]
    activacion: list[Step]
    siguiente_paso: str | None


def _any(session: Session, statement: object) -> bool:
    return bool(session.execute(select(exists(statement))).scalar())  # type: ignore[arg-type]


def build_overview(session: Session, tenant_id: uuid.UUID) -> Overview:
    profile = BusinessProfileRepository(session, tenant_id).current()
    agent = AgentConfigRepository(session, tenant_id).current()
    sections = BusinessInfoRepository(session, tenant_id).sections()
    has_payment = PaymentMethodRepository(session, tenant_id).count_active() > 0
    has_product = (
        ProductRepository(session, tenant_id).count(
            Product.deleted_at.is_(None), Product.is_active.is_(True)
        )
        > 0
    )

    def mine(model: type) -> object:
        return select(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]

    has_faq = _any(session, mine(Faq))
    has_promo = _any(session, mine(Promotion))
    has_media = _any(session, mine(MediaAsset))
    has_knowledge = _any(
        session,
        select(KnowledgeSource).where(
            KnowledgeSource.tenant_id == tenant_id, KnowledgeSource.deleted_at.is_(None)
        ),
    )
    active_situation = _any(
        session,
        select(Situation).where(Situation.tenant_id == tenant_id, Situation.estado == "activa"),
    )
    sandbox = select(Conversation.id).where(
        Conversation.tenant_id == tenant_id, Conversation.channel == "sandbox"
    )
    tried_simulator = _any(
        session,
        select(ConversationStageEvent).where(
            ConversationStageEvent.tenant_id == tenant_id,
            ConversationStageEvent.conversation_id.in_(sandbox),
            ConversationStageEvent.to_stage.in_(PAST_IDENTIFICAR),
        ),
    )
    tried_situation = _any(
        session,
        select(AgentTrace)
        .join(Message, Message.id == AgentTrace.message_id)
        .where(
            AgentTrace.tenant_id == tenant_id,
            AgentTrace.situaciones.is_not(None),
            Message.conversation_id.in_(sandbox),
        ),
    )
    connected = WhatsAppNumberRepository(session, tenant_id).has_connected()

    detail: dict[str, SectionState] = {
        "metodos_pago": "completa" if has_payment else "vacia",
        "locales": "completa" if "locales" in sections else "vacia",
        "entregas": "completa" if "entregas" in sections else "vacia",
        "tipos_cliente": "completa" if "tipos_cliente" in sections else "vacia",
        "faqs": "completa" if has_faq else "vacia",
        "promociones": "completa" if has_promo else "vacia",
        "devoluciones": "completa" if "devoluciones" in sections else "vacia",
        "contacto": "completa" if "contacto" in sections else "vacia",
    }
    filled = sum(state == "completa" for state in detail.values())
    info_state: SectionState = (
        "completa" if filled == len(detail) else "parcial" if filled else "vacia"
    )
    secciones = [
        Section(
            "informacion_basica",
            "completa" if profile and profile.business_name and profile.industry else "vacia",
        ),
        Section("informacion_negocio", info_state, detail),
        Section("comportamiento", "completa" if agent and agent.agent_name else "vacia"),
        Section("proceso_venta", "completa" if agent else "vacia"),
        Section("productos", "completa" if has_product else "vacia"),
        Section("situaciones", "completa" if active_situation else "opcional"),
        Section("multimedia", "completa" if has_media else "opcional"),
        Section("conocimiento", "completa" if has_knowledge else "opcional"),
    ]
    capabilities = agent.capacidades if agent is not None else DEFAULT_CAPABILITIES
    sells_ = sells(capabilities)
    quotes = "cotizar" in capabilities
    needs_catalogue = leads_to_a_sale(capabilities)
    contact = sections.get("contacto") or {}
    has_contact = bool(contact.get("telefono_humano") or contact.get("correo"))

    # (key, done, group, required): only what this agent's capabilities ask for.
    plan: list[tuple[str, bool, str, bool]] = [
        ("negocio", bool(profile and profile.business_name and profile.industry), "probar", True),
        ("agente", bool(agent and agent.agent_name), "probar", True),
    ]
    if needs_catalogue:
        plan.append(("productos", has_product, "probar", True))
    if sells_:
        plan.append(("metodos_pago", has_payment, "probar", True))
    plan.append(("conocimiento", has_knowledge, "probar", False))
    plan.append(("informacion", info_state != "vacia", "probar", False))
    if quotes:
        plan.append(("contacto", has_contact, "probar", False))
    plan += [
        ("probar_simulador", tried_simulator, "probar", False),
        ("conectar_whatsapp", connected, "clientes", True),
        ("primera_situacion", active_situation, "mejorar", False),
        ("probar_situacion", tried_situation, "mejorar", False),
    ]
    steps = [
        Step(key, done_, STEP_ACTIONS[key], grupo=group, requerido=required)
        for key, done_, group, required in plan
    ]
    weight = {True: 2, False: 1}
    total = sum(weight[step.requerido] for step in steps)
    earned = sum(weight[step.requerido] for step in steps if step.hecho)
    return Overview(
        progreso=round(100 * earned / total),
        secciones=secciones,
        activacion=steps,
        # A required step first: what blocks the agent comes before what improves it.
        siguiente_paso=next(
            (s.paso for s in steps if not s.hecho and s.requerido),
            next((s.paso for s in steps if not s.hecho), None),
        ),
    )
