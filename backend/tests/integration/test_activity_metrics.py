"""What the assistant did besides answering: appointments, quotes, qualified customers (019, G)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.crm import Lead
from app.process.templates.base import Stage
from app.services.metrics.activity import what_it_did
from tests.integration.test_crm_leads import _leads, _save
from tests.integration.test_crm_leads import _setup as _lead_setup
from tests.integration.test_scheduling import (
    _business,
    _scheduling,
    _service,
    at,
)


def _range() -> tuple[datetime, datetime]:
    today = datetime.now(UTC)
    return today - timedelta(days=1), today


class TestWhatItDid:
    def test_a_business_that_did_nothing_has_nothing_to_show(self, db: Session) -> None:
        tenant = _business(db)
        start, end = _range()

        assert what_it_did(db, tenant.id, start.date(), end.date()) == {}

    def test_appointments_are_counted_by_who_booked_and_how_they_went(self, db: Session) -> None:
        tenant = _business(db)
        service = _service(db, tenant)
        scheduling = _scheduling(db, tenant)
        by_the_assistant = scheduling.book(
            service, at(10, days=1), by="agent", nombre_cliente="Ana"
        )
        scheduling.book(service, at(12, days=1), by="agent", nombre_cliente="Beto")
        past = scheduling.book(service, at(15, days=1), by="user", nombre_cliente="Carla")
        # One of them already took place and a customer did not come.
        now = datetime.now(UTC)
        past.status = "no_asistio"
        past.starts_at, past.ends_at, past.ocupa_hasta = (
            now - timedelta(hours=3),
            now - timedelta(hours=2),
            now - timedelta(hours=2),
        )
        by_the_assistant.status = "completada"
        by_the_assistant.starts_at, by_the_assistant.ends_at, by_the_assistant.ocupa_hasta = (
            now - timedelta(hours=5),
            now - timedelta(hours=4),
            now - timedelta(hours=4),
        )
        db.flush()
        start, end = _range()

        done = what_it_did(db, tenant.id, start.date(), end.date())

        assert done["citas"] == {
            "agendadas": 3,
            "por_el_asistente": 2,
            "completadas": 1,
            "faltas": 1,
        }
        assert "cotizaciones" not in done and "calificados" not in done

    def test_quotes_and_qualified_customers_come_from_the_leads(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        _save(
            states,
            conversation,
            Stage.IDENTIFICAR,
            lead_calificado={
                "interes": "alto",
                "motivo": "pide propuesta",
                "respuestas": {},
                "en": datetime.now(UTC).isoformat(),
            },
        )
        (lead,) = _leads(db, tenant)
        assert isinstance(lead, Lead)
        lead.quote_requested_at = datetime.now(UTC)
        lead.quote_sent_at = datetime.now(UTC)
        db.flush()
        start, end = _range()

        done = what_it_did(db, tenant.id, start.date(), end.date())

        assert done["cotizaciones"] == {"pedidas": 1, "enviadas": 1}
        assert done["calificados"] == {"total": 1, "calientes": 1, "tibios": 0, "frios": 0}
        assert "citas" not in done

    async def test_returns_are_counted_by_how_they_were_decided(self, db: Session) -> None:
        from decimal import Decimal

        from app.agent.tools.aftersale import solicitar_devolucion
        from app.agent.tools.registry import ToolContext
        from app.models.business import Product
        from app.providers.llm import MockLLMProvider
        from tests.integration.test_postsale_tool import _order
        from tests.integration.test_turn_pipeline import _conversation, _tenant

        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant, channel="whatsapp")
        polo = Product(tenant_id=tenant.id, name="Polo", price=Decimal("35"))
        db.add(polo)
        db.flush()
        _order(db, tenant.id, conversation, "completado", lines=(("Polo", 1, polo),))
        await solicitar_devolucion(
            ToolContext(
                session=db,
                tenant_id=tenant.id,
                llm=MockLLMProvider(),
                conversation_id=conversation.id,
            ),
            {"tipo": "cambio", "motivo": "defectuoso", "detalle": "Roto"},
        )
        start, end = _range()

        done = what_it_did(db, tenant.id, start.date(), end.date())

        assert done["devoluciones"] == {
            "solicitadas": 1,
            "aprobadas": 0,
            "rechazadas": 0,
            "pendientes": 1,
        }
