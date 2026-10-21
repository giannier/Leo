"""The agent books appointments through three tools (feature 019, phase D)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.tools.definitions import default_catalog
from app.agent.tools.registry import ToolContext
from app.agent.tools.scheduling import agendar_cita, consultar_horarios, gestionar_cita
from app.models.business import Product
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.process.facts import Facts
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.providers.llm import MockLLMProvider
from app.scheduling.service import Scheduling
from app.scheduling.text import slot_token
from tests.integration.test_scheduling import LIMA, NOW, _business, _service, at
from tests.integration.test_turn_pipeline import _conversation


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Scheduling, "now", lambda self: NOW)


def _context(db: Session, tenant: Tenant, conversation: Conversation, **extra: Any) -> ToolContext:
    return ToolContext(
        session=db,
        tenant_id=tenant.id,
        llm=MockLLMProvider(),
        conversation_id=conversation.id,
        **extra,
    )


def _shop(db: Session) -> tuple[Tenant, Product, Conversation]:
    tenant = _business(db)
    service = _service(db, tenant)
    Scheduling(db, tenant.id).ensure_resource()
    return tenant, service, _conversation(db, tenant, channel="whatsapp")


class TestOffering:
    async def test_at_most_three_slots_the_model_can_pass_back(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        context = _context(db, tenant, conversation)

        result = await consultar_horarios(context, {"producto_id": str(service.id)})

        assert result.status == "hecho" and result.texto is not None
        assert result.texto.count("- slot ") == 3
        assert f"slot {slot_token(at(10), LIMA)}" in result.texto  # the first free hour

    async def test_a_service_that_is_not_bookable_is_refused(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        service.agendable = False
        db.flush()

        result = await consultar_horarios(
            _context(db, tenant, conversation), {"producto_id": str(service.id)}
        )

        assert result.status == "no_disponible" and "derivar_a_humano" in (result.decir or "")

    async def test_nothing_free_sends_the_customer_to_a_person(self, db: Session) -> None:
        tenant = _business(db, hours={})
        service = _service(db, tenant)
        conversation = _conversation(db, tenant, channel="whatsapp")

        result = await consultar_horarios(
            _context(db, tenant, conversation), {"producto_id": str(service.id)}
        )

        assert result.status == "no_disponible" and "una persona" in (result.decir or "")


class TestBooking:
    async def test_the_chosen_slot_is_booked_for_this_customer(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        context = _context(db, tenant, conversation)

        result = await agendar_cita(
            context, {"producto_id": str(service.id), "slot": slot_token(at(10), LIMA)}
        )

        assert result.status == "hecho"
        (booked,) = Scheduling(db, tenant.id).appointments.between(at(0), at(23))
        assert booked.starts_at == at(10) and booked.contact_id == conversation.contact_id
        assert booked.created_by == "agent" and booked.conversation_id == conversation.id
        assert context.facts_patch["cita_agendada"] == at(10).isoformat()
        assert "lunes 26 de octubre, 10:00" in (result.decir or "")

    async def test_a_minute_that_just_went_gets_the_next_options_at_once(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        other = _conversation(db, tenant, channel="whatsapp")
        token = slot_token(at(10), LIMA)
        await agendar_cita(
            _context(db, tenant, other), {"producto_id": str(service.id), "slot": token}
        )

        result = await agendar_cita(
            _context(db, tenant, conversation), {"producto_id": str(service.id), "slot": token}
        )

        assert result.status == "no_disponible"
        assert result.texto is not None and "se acaba de ocupar" in result.texto
        assert result.texto.count("- slot ") >= 1 and token not in result.texto.split("\n", 1)[1]

    async def test_a_made_up_slot_is_an_error_not_a_booking(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)

        result = await agendar_cita(
            _context(db, tenant, conversation), {"producto_id": str(service.id), "slot": "mañana"}
        )

        assert result.status == "error"
        assert not Scheduling(db, tenant.id).appointments.between(at(0), at(23, days=30))

    async def test_the_rules_come_back_as_words_for_the_customer(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)

        result = await agendar_cita(
            _context(db, tenant, conversation),
            {"producto_id": str(service.id), "slot": slot_token(at(9), LIMA)},
        )

        assert result.status == "no_disponible" and "anticipación" in (result.decir or "")

    async def test_the_simulator_checks_everything_and_writes_nothing(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        context = _context(db, tenant, conversation, sandbox=True)

        result = await agendar_cita(
            context, {"producto_id": str(service.id), "slot": slot_token(at(10), LIMA)}
        )

        assert result.status == "hecho"
        assert not Scheduling(db, tenant.id).appointments.between(at(0), at(23, days=30))


class TestManagingMyAppointments:
    async def _booked(self, db: Session) -> tuple[Tenant, Product, Conversation, str]:
        tenant, service, conversation = _shop(db)
        await agendar_cita(
            _context(db, tenant, conversation),
            {"producto_id": str(service.id), "slot": slot_token(at(10), LIMA)},
        )
        (booked,) = Scheduling(db, tenant.id).appointments.between(at(0), at(23))
        return tenant, service, conversation, str(booked.id)

    async def test_see_move_confirm_and_cancel(self, db: Session) -> None:
        tenant, service, conversation, booked_id = await self._booked(db)
        context = _context(db, tenant, conversation)

        seen = await gestionar_cita(context, {"accion": "ver"})
        moved = await gestionar_cita(
            context, {"accion": "reprogramar", "slot": slot_token(at(11), LIMA)}
        )
        confirmed = await gestionar_cita(context, {"accion": "confirmar", "cita_id": booked_id})
        cancelled = await gestionar_cita(context, {"accion": "cancelar", "motivo": "viaja"})

        assert seen.texto is not None and booked_id in seen.texto
        assert (
            moved.status == "hecho"
            and "10:00" not in (moved.decir or "")
            and "11:00" in (moved.decir or "")
        )
        assert confirmed.status == "hecho" and cancelled.status == "hecho"
        (row,) = Scheduling(db, tenant.id).appointments.between(at(0), at(23))
        assert row.status == "cancelada" and row.starts_at == at(11)

    async def test_nobody_touches_somebody_else_s_appointment(self, db: Session) -> None:
        tenant, service, conversation, booked_id = await self._booked(db)
        stranger = _context(db, tenant, _conversation(db, tenant, channel="whatsapp"))

        seen = await gestionar_cita(stranger, {"accion": "ver"})
        cancelled = await gestionar_cita(stranger, {"accion": "cancelar", "cita_id": booked_id})

        assert seen.status == "no_disponible"
        assert cancelled.status == "no_disponible"
        (row,) = Scheduling(db, tenant.id).appointments.between(at(0), at(23))
        assert row.status == "agendada"


class TestTheToolsOnlyExistWhenTheBusinessBooks:
    @pytest.mark.parametrize("agendar", [False, True])
    def test_the_registry(self, agendar: bool) -> None:
        template = TEMPLATES["servicios"]
        options = replace(template.resolve(), agendar=agendar)

        registry = default_catalog().for_stage(template, Stage.IDENTIFICAR, Facts(), options)

        scheduling = {"consultar_horarios", "agendar_cita", "gestionar_cita"}
        names = set(registry.names())

        assert (scheduling <= names) if agendar else not (scheduling & names)


class TestWhatTheModelActuallyWrites:
    """Found by the evals against the real model: it names the service instead of passing its id,
    calls the id `servicio_id`, and writes the slot with a space."""

    async def test_the_service_may_be_named_instead_of_identified(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        context = _context(db, tenant, conversation)

        result = await consultar_horarios(context, {"producto_id": "consulta dental"})
        missing = await consultar_horarios(context, {"producto_id": "algo que no existe"})

        assert result.status == "hecho" and "Consulta" in (result.texto or "")
        assert missing.status == "error"  # a typo is retried, it does not send the customer away

    async def test_the_old_name_of_the_argument_and_a_slot_with_a_space_still_book(
        self, db: Session
    ) -> None:
        tenant, service, conversation = _shop(db)
        token = slot_token(at(10), LIMA).replace("T", " ")

        result = await agendar_cita(
            _context(db, tenant, conversation), {"servicio_id": str(service.id), "slot": token}
        )

        assert result.status == "hecho"
        (booked,) = Scheduling(db, tenant.id).appointments.between(at(0), at(23))
        assert booked.starts_at == at(10)

    async def test_a_name_that_matches_two_services_is_not_guessed(self, db: Session) -> None:
        tenant, service, conversation = _shop(db)
        _service(db, tenant, name="Consulta de seguimiento")

        result = await consultar_horarios(
            _context(db, tenant, conversation), {"producto_id": "consulta"}
        )

        assert result.status == "error"
