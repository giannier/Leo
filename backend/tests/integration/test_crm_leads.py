"""Leads that move with the sales process, and that a person can correct (Feature 012)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.conversation_state import FunnelCode
from app.models.crm import Lead, LeadEvent
from app.models.orders import Order, OrderItem
from app.models.tenant import Tenant
from app.models.user import User
from app.process.state import StateRepo
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.services.crm import CrmError, LeadService
from tests.integration.test_turn_pipeline import _conversation, _tenant


def _setup(db: Session, *, channel: str = "whatsapp") -> tuple[Tenant, Conversation, StateRepo]:
    tenant = _tenant(db, template="productos")
    conversation = _conversation(db, tenant, channel=channel)
    states = StateRepo(db, tenant.id)
    states.get_or_create(conversation, TEMPLATES["productos"])
    return tenant, conversation, states


def _save(
    states: StateRepo,
    conversation: Conversation,
    stage: Stage,
    reason: str = "turno",
    **facts: object,
) -> None:
    state = states.get(conversation.id)
    assert state is not None
    states.save(
        conversation.id,
        stage=stage,
        funnel=FunnelCode.ATENDIDO,
        facts_patch=dict(facts) or None,
        reason=reason,
        expected_version=state.version,
        force_event=True,
    )


def _order(
    db: Session, tenant: Tenant, conversation: Conversation, total: str, estado: str = "borrador"
) -> Order:
    from app.repositories.orders import OrderRepository

    order = OrderRepository(db, tenant.id).add_numbered(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        moneda="PEN",
        subtotal=Decimal(total),
        total=Decimal(total),
        estado=estado,
    )
    db.add(
        OrderItem(
            tenant_id=tenant.id,
            order_id=order.id,
            nombre="Paquete 5 certificaciones",
            precio_unitario=Decimal(total),
            cantidad=1,
            subtotal=Decimal(total),
        )
    )
    db.flush()
    return order


def _user(db: Session) -> uuid.UUID:
    user = User(
        email=f"crm-{uuid.uuid4().hex[:10]}@example.com", password_hash="x", full_name="Ana"
    )
    db.add(user)
    db.flush()
    return user.id


def _leads(db: Session, tenant: Tenant) -> list[Lead]:
    return list(
        db.execute(
            select(Lead).where(Lead.tenant_id == tenant.id).order_by(Lead.created_at)
        ).scalars()
    )


def _stage_key(db: Session, lead: Lead) -> str:
    from app.models.crm import PipelineStage

    stage = db.get(PipelineStage, lead.stage_id)
    assert stage is not None
    return stage.key


class TestTheProcessMovesTheLead:
    def test_a_whole_sale(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)

        _save(states, conversation, Stage.IDENTIFICAR)
        assert _leads(db, tenant) == []  # a greeting is not a lead

        _save(states, conversation, Stage.IDENTIFICAR, vio_productos=True)
        (lead,) = _leads(db, tenant)
        assert _stage_key(db, lead) == "nuevo" and lead.status == "active"

        order = _order(db, tenant, conversation, "450.00")
        _save(states, conversation, Stage.PEDIDO, reason="herramienta:gestionar_pedido")
        db.refresh(lead)
        assert _stage_key(db, lead) == "interesado"
        assert lead.amount_estimated == Decimal("450.00") and lead.moneda == "PEN"
        assert lead.product_interest == "Paquete 5 certificaciones"

        _save(states, conversation, Stage.COBRO)
        db.refresh(lead)
        assert _stage_key(db, lead) == "por_pagar"

        order.estado = "confirmado"
        db.flush()
        _save(states, conversation, Stage.CERRADO, reason="operador:confirmar")
        db.refresh(lead)
        assert _stage_key(db, lead) == "ganado" and lead.status == "won"
        assert lead.amount_real == Decimal("450.00") and lead.closed_at is not None

        # More events on the same won sale do not count it twice.
        _save(states, conversation, Stage.CERRADO, reason="operador:nota")
        assert len(_leads(db, tenant)) == 1

        events = list(
            db.execute(
                select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.created_at)
            ).scalars()
        )
        assert [(e.from_stage, e.to_stage, e.actor) for e in events] == [
            (None, "nuevo", "agent"),
            ("nuevo", "interesado", "agent"),
            ("interesado", "por_pagar", "agent"),
            ("por_pagar", "ganado", "system"),
        ]

    def test_never_backwards(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _order(db, tenant, conversation, "100.00")
        _save(states, conversation, Stage.COBRO)
        _save(states, conversation, Stage.PEDIDO)
        (lead,) = _leads(db, tenant)
        assert _stage_key(db, lead) == "por_pagar"

    def test_a_stale_draft_loses_the_lead_for_no_reply(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _order(db, tenant, conversation, "80.00")
        _save(states, conversation, Stage.PEDIDO)
        _save(states, conversation, Stage.IDENTIFICAR, reason="pedido_descartado")
        (lead,) = _leads(db, tenant)
        assert lead.status == "lost" and lead.lost_reason == "dejo_de_responder"
        assert _stage_key(db, lead) == "perdido"

    def test_the_simulator_never_makes_leads(self, db: Session) -> None:
        tenant, conversation, states = _setup(db, channel="sandbox")
        _save(states, conversation, Stage.IDENTIFICAR, vio_productos=True)
        assert _leads(db, tenant) == []


class TestThePersonWins:
    def test_after_a_person_moves_it_only_evidence_moves_it(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _save(states, conversation, Stage.IDENTIFICAR, vio_productos=True)
        (lead,) = _leads(db, tenant)
        service = LeadService(db, tenant.id)
        board = service.board()
        user = _user(db)
        service.move(lead, board.semantic("nuevo").id, by=user)
        db.flush()

        _save(states, conversation, Stage.ASESORIA)  # no evidence: stays where the person left it
        db.refresh(lead)
        assert _stage_key(db, lead) == "nuevo"

        _order(db, tenant, conversation, "200.00")
        _save(states, conversation, Stage.COBRO)  # an order is evidence
        db.refresh(lead)
        assert _stage_key(db, lead) == "por_pagar"

    def test_a_person_s_lead_is_not_lost_by_the_process(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _order(db, tenant, conversation, "80.00")
        _save(states, conversation, Stage.PEDIDO)
        (lead,) = _leads(db, tenant)
        lead.moved_by_person = True
        db.flush()
        _save(states, conversation, Stage.IDENTIFICAR, reason="pedido_descartado")
        db.refresh(lead)
        assert lead.status == "active"

    def test_no_es_lead_holds_until_there_is_an_order(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _save(states, conversation, Stage.IDENTIFICAR, vio_productos=True)
        (lead,) = _leads(db, tenant)
        LeadService(db, tenant.id).discard(lead, by=_user(db))
        db.flush()

        _save(states, conversation, Stage.ASESORIA)
        assert len(_leads(db, tenant)) == 1

        _order(db, tenant, conversation, "60.00")
        _save(states, conversation, Stage.COBRO)
        leads = _leads(db, tenant)
        assert len(leads) == 2 and leads[1].previous_lead_id == lead.id

    def test_losing_needs_a_known_reason(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _save(states, conversation, Stage.IDENTIFICAR, vio_productos=True)
        (lead,) = _leads(db, tenant)
        service = LeadService(db, tenant.id)
        with pytest.raises(CrmError):
            service.move(
                lead,
                service.board().semantic("perdido").id,
                by=_user(db),
                lost_reason="inventado",
            )
        service.move(
            lead, service.board().semantic("perdido").id, by=_user(db), lost_reason="precio"
        )
        assert lead.status == "lost" and lead.lost_reason == "precio"


class TestTheBoard:
    def test_stages_rename_add_and_remove(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = LeadService(db, tenant.id)
        current = [{"key": s.key, "label": s.label} for s in service.board().stages]
        current[0]["label"] = "Llegó"
        current.insert(2, {"label": "Cotización enviada"})
        stages = service.save_stages(current)
        assert [s.label for s in stages][:3] == ["Llegó", "Interesado", "Cotización enviada"]
        custom = stages[2]
        assert custom.semantic is None and custom.key == "cotizacion_enviada"
        board = service.board()
        assert (
            board.rank(board.semantic("interesado"))
            < board.rank(custom)
            < board.rank(board.semantic("por_pagar"))
        )

        without = [{"key": s.key, "label": s.label} for s in stages if s.semantic is not None]
        assert len(service.save_stages(without)) == 5

    def test_the_fixed_stages_stay(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = LeadService(db, tenant.id)
        stages = [{"key": s.key, "label": s.label} for s in service.board().stages]
        with pytest.raises(CrmError):
            service.save_stages(stages[1:])
        with pytest.raises(CrmError):
            service.save_stages(list(reversed(stages)))
        with pytest.raises(CrmError):
            service.save_stages(stages + [{"label": f"x{i}"} for i in range(4)])

    def test_the_history_is_append_only(self, db: Session) -> None:
        tenant, conversation, states = _setup(db)
        _save(states, conversation, Stage.IDENTIFICAR, vio_productos=True)
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(
                text("UPDATE lead_events SET cause = 'x' WHERE tenant_id = :t"),
                {"t": tenant.id},
            )
