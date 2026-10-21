"""Questions to try the assistant with, made from the business's own data (feature 019, phase C).

The owner is asked to try the assistant before turning it on, and "what do I ask?" is the hard
part. The questions come from what the business loaded (its products, payment methods, delivery
zones, bookable services) plus the ones every business gets (price objection, a person, a robot).
Nothing here calls a model: the owner taps a question in the simulator and reads the answer; the
tap costs what any simulator message costs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.business import Product
from app.repositories.business import AgentConfigRepository
from app.repositories.payment import PaymentMethodRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository

MAX_QUESTIONS = 10


@dataclass(frozen=True)
class TestQuestion:
    texto: str
    sobre: (
        str  # what it checks: "precio", "cotizacion", "agenda", "pago", "entrega", "horario", ...
    )


def build(session: Session, tenant_id: uuid.UUID) -> list[TestQuestion]:
    questions: list[TestQuestion] = []
    agent = AgentConfigRepository(session, tenant_id).current()
    capacities = set(agent.capacidades or ()) if agent else set()
    products = [
        p
        for p in ProductRepository(session, tenant_id).list_active(limit=12)
        if isinstance(p, Product) and p.deleted_at is None
    ]
    fixed = [p for p in products if p.modo_venta == "precio_fijo" and not p.agendable]
    quoted = [p for p in products if p.modo_venta == "cotizacion"]
    bookable = [p for p in products if p.agendable and p.duracion_min]

    if fixed:
        questions.append(TestQuestion(f"¿Cuánto cuesta {fixed[0].name}?", "precio"))
        if len(fixed) > 1:
            questions.append(
                TestQuestion(
                    f"¿Qué diferencia hay entre {fixed[0].name} y {fixed[1].name}?", "producto"
                )
            )
        questions.append(TestQuestion(f"Quiero {fixed[0].name}, ¿cómo lo compro?", "pedido"))
    if quoted and ("cotizar" in capacities or not capacities):
        questions.append(TestQuestion(f"Necesito una cotización de {quoted[0].name}", "cotizacion"))
    if bookable and "agendar" in capacities:
        questions.append(TestQuestion(f"Quiero agendar {bookable[0].name} esta semana", "agenda"))
        questions.append(TestQuestion("Necesito cambiar mi cita para otro día", "agenda"))
    if PaymentMethodRepository(session, tenant_id).count_active() > 0:
        questions.append(TestQuestion("¿Cómo puedo pagar?", "pago"))
    delivery = BusinessInfoRepository(session, tenant_id).sections().get("entregas") or {}
    zones = delivery.get("zonas") if isinstance(delivery, dict) else None
    if delivery.get("hace_delivery") and zones:
        questions.append(
            TestQuestion(f"¿Hacen delivery a {zones[0].get('nombre', 'mi zona')}?", "entrega")
        )
    questions.append(TestQuestion("¿Dónde están ubicados y a qué hora atienden?", "horario"))
    questions.append(TestQuestion("Me parece caro, ¿hay algún descuento?", "objecion"))
    questions.append(TestQuestion("Quiero hablar con una persona", "derivacion"))
    questions.append(TestQuestion("¿Eres una persona o un robot?", "transparencia"))
    return questions[:MAX_QUESTIONS]
