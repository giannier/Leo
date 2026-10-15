"""How far a business got configuring its assistant (Feature 004 §1, TASK-437).

Two lists for the panel's home: the state of each section, and the activation
steps in order with the next one to do. The 017 adds two steps: activate the
first situation, and try a situation in the simulator.

Progress: the activation steps weigh 80 % evenly; the optional sections
(business information complete, media, knowledge) share the other 20 %.

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
    "productos": "/productos",
    "metodos_pago": "/asistente/informacion",
    "proceso_venta": "/asistente/proceso",
    "primera_situacion": "/asistente/situaciones",
    "probar_simulador": "/asistente/simulador",
    "probar_situacion": "/asistente/situaciones",
    "conectar_whatsapp": "/whatsapp",
}


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
    done = {
        "productos": has_product,
        "metodos_pago": has_payment,
        "proceso_venta": agent is not None,
        "primera_situacion": active_situation,
        "probar_simulador": tried_simulator,
        "probar_situacion": tried_situation,
        "conectar_whatsapp": connected,
    }
    steps = [Step(key, done[key], STEP_ACTIONS[key]) for key in STEP_ACTIONS]
    optional = [info_state == "completa", has_media, has_knowledge]
    progress = round(
        80 * sum(s.hecho for s in steps) / len(steps) + 20 * sum(optional) / len(optional)
    )
    return Overview(
        progreso=progress,
        secciones=secciones,
        activacion=steps,
        siguiente_paso=next((s.paso for s in steps if not s.hecho), None),
    )
