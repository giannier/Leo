"""Quoting for real: the quote a customer asks for lives on the lead (feature 019, phase B)."""

from __future__ import annotations

# ruff: noqa: F811
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.catalog import _product_data, _product_line
from app.agent.tools.handover import _quote_data
from app.models.business import Product
from app.models.conversation_state import FunnelCode
from app.models.crm import LeadEvent
from app.process.state import StateRepo
from app.process.templates.base import Stage
from app.services.crm import CrmError, LeadService
from tests.integration.test_crm_api import L, _lead, _setup
from tests.integration.test_crm_leads import _leads, _save, _user
from tests.integration.test_crm_leads import _setup as _lead_setup


def _handed_over(*, since: datetime | None = None, **extra: object) -> dict[str, object]:
    return {
        "motivo": "cotizacion",
        "resumen": "Quiere un sistema de turnos para su clínica",
        "datos": {"personas": "20", "para cuándo": "marzo"},
        "desde": (since or datetime.now(UTC)).isoformat(),
        "hasta": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
        **extra,
    }


class TestTheHandOverBecomesTheQuote:
    def test_the_agent_s_hand_over_is_the_leads_quote(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)

        _save(states, conversation, Stage.ASESORIA, derivado=_handed_over())

        (lead,) = _leads(db, tenant)
        assert lead.quote_status == "solicitada"
        assert lead.quote_summary == "Quiere un sistema de turnos para su clínica"
        assert lead.quote_data == {"personas": "20", "para cuándo": "marzo"}
        assert lead.quote_requested_at is not None and lead.quote_sent_at is None

    def test_another_reason_makes_no_quote(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)

        _save(
            states,
            conversation,
            Stage.ASESORIA,
            derivado={**_handed_over(), "motivo": "asesoria"},
        )

        (lead,) = _leads(db, tenant)
        assert lead.quote_status is None

    def test_a_person_sends_it_and_a_new_request_starts_a_new_quote(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        _save(states, conversation, Stage.ASESORIA, derivado=_handed_over())
        (lead,) = _leads(db, tenant)

        LeadService(db, tenant.id).quote_sent(
            lead, by=_user(db), amount=Decimal("1200.00"), moneda="PEN"
        )
        assert lead.quote_status == "enviada" and lead.quote_sent_at is not None
        assert lead.amount_estimated == Decimal("1200.00") and lead.moneda == "PEN"

        # The same hand-over written again is not a new request...
        _save(
            states,
            conversation,
            Stage.ASESORIA,
            derivado=_handed_over(since=lead.quote_requested_at),
        )
        db.refresh(lead)
        assert lead.quote_status == "enviada"

        # ...a later one is: the customer is waiting for another quote.
        _save(
            states,
            conversation,
            Stage.ASESORIA,
            derivado=_handed_over(
                since=datetime.now(UTC) + timedelta(minutes=5), resumen="Y un módulo de pagos"
            ),
        )
        db.refresh(lead)
        assert lead.quote_status == "solicitada" and lead.quote_sent_at is None
        assert lead.quote_summary == "Y un módulo de pagos"

    def test_sending_needs_a_quote_asked_for(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        _save(states, conversation, Stage.ASESORIA, derivado={**_handed_over(), "motivo": "otro"})
        (lead,) = _leads(db, tenant)

        with pytest.raises(CrmError) as refused:
            LeadService(db, tenant.id).quote_sent(lead, by=_user(db))

        assert refused.value.code == "no_quote"

    def test_both_moves_are_in_the_history(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        _save(states, conversation, Stage.ASESORIA, derivado=_handed_over())
        (lead,) = _leads(db, tenant)
        LeadService(db, tenant.id).quote_sent(lead, by=_user(db))

        causes = [
            e.cause
            for e in db.execute(
                select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.created_at)
            ).scalars()
        ]

        assert (
            "turno:cotizacion_solicitada" in causes and causes[-1] == "persona:cotizacion_enviada"
        )


class TestTheQuoteInTheApi:
    async def test_the_lead_shows_the_quote_and_a_person_marks_it_sent(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _ = await _setup(api, mail, db)
        conversation = _lead(db, tenant_id)
        states = StateRepo(db, tenant_id)
        state = states.get(conversation.id)
        assert state is not None
        states.save(
            conversation.id,
            stage=Stage.ASESORIA,
            funnel=FunnelCode.NEGOCIACION,
            facts_patch={"derivado": _handed_over()},
            reason="turno",
            expected_version=state.version,
        )
        db.commit()
        lead_id = (await api.get(L, headers=owner)).json()["items"][0]["id"]

        asked = (await api.get(f"{L}/{lead_id}", headers=owner)).json()["lead"]["cotizacion"]
        assert asked["estado"] == "solicitada" and asked["datos"]["personas"] == "20"
        assert asked["dias_desde_envio"] is None

        sent = await api.post(
            f"{L}/{lead_id}/quote/sent", headers=owner, json={"monto": "1200.00", "moneda": "PEN"}
        )

        assert sent.status_code == 200
        lead = sent.json()["lead"]
        assert (
            lead["cotizacion"]["estado"] == "enviada"
            and lead["cotizacion"]["dias_desde_envio"] == 0
        )
        assert lead["amount_estimated"] == "1200.00"

    async def test_without_a_quote_asked_for_it_is_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _ = await _setup(api, mail, db)
        _lead(db, tenant_id)
        lead_id = (await api.get(L, headers=owner)).json()["items"][0]["id"]

        refused = await api.post(f"{L}/{lead_id}/quote/sent", headers=owner, json={})

        assert refused.status_code == 422 and refused.json()["error"] == "no_quote"


class TestWhatToAskForAService:
    def test_the_hand_over_data_is_short_unique_and_at_most_eight(self) -> None:
        raw = [{"dato": f"dato {i}", "valor": "x" * 300} for i in range(12)]
        raw += [
            {"dato": "dato 0", "valor": "repetido"},
            "no es un dato",
            {"dato": "", "valor": "y"},
        ]

        data = _quote_data(raw)

        assert len(data) == 8 and all(len(v) <= 120 for v in data.values())
        assert _quote_data("nada") == {}

    def test_a_quoted_service_tells_the_agent_what_to_ask(self) -> None:
        product = Product(
            name="Sistema de turnos",
            price=Decimal("0"),
            modo_venta="cotizacion",
            periodo="unico",
            es_digital=False,
            destacado=False,
            tags=[],
            datos_a_pedir=["Para cuántas personas", "Para cuándo"],
        )

        line = _product_line(_product_data(product))

        assert "uno por mensaje" in line
        assert "Para cuántas personas; Para cuándo" in line

    def test_a_fixed_price_item_does_not_ask_anything(self) -> None:
        product = Product(
            name="Hosting",
            price=Decimal("30"),
            modo_venta="precio_fijo",
            periodo="mes",
            es_digital=True,
            destacado=False,
            tags=[],
            datos_a_pedir=["Para cuándo"],
        )

        assert "uno por mensaje" not in _product_line(_product_data(product))

    async def test_the_owner_sets_up_to_six_questions_per_service(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _ = await _setup(api, mail, db)
        body = {"name": "Sistema de turnos", "price": "0", "modo_venta": "cotizacion"}

        created = await api.post(
            "/api/v1/tenant/products",
            headers=owner,
            json={**body, "datos_a_pedir": ["Para cuándo"]},
        )
        too_many = await api.post(
            "/api/v1/tenant/products",
            headers=owner,
            json={**body, "datos_a_pedir": [f"Pregunta {i}" for i in range(7)]},
        )

        assert created.status_code == 201 and created.json()["datos_a_pedir"] == ["Para cuándo"]
        assert too_many.status_code == 400
        edited = await api.put(
            f"/api/v1/tenant/products/{created.json()['id']}",
            headers=owner,
            json={"datos_a_pedir": ["Para cuándo", "Cuántas personas"]},
        )
        assert edited.json()["datos_a_pedir"] == ["Para cuándo", "Cuántas personas"]
