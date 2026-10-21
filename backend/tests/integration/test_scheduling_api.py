"""The agenda's API: setup, manual appointments, moves and who may do what (feature 019, D)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.business import BusinessProfile
from app.models.campaigns import MessageTemplate
from tests.integration.test_orders_api import operator_for

LIMA = ZoneInfo("America/Lima")
S = "/api/v1/tenant/scheduling"
A = "/api/v1/tenant/appointments"
HOURS = {d: [["09:00", "13:00"], ["15:00", "19:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}


def _next_weekday(days_ahead: int = 4) -> str:
    """A weekday at least `days_ahead` days from now, as `YYYY-MM-DD` in Lima."""
    day = (datetime.now(UTC) + timedelta(days=days_ahead)).astimezone(LIMA).date()
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day.isoformat()


async def _shop(
    api: AsyncClient, mail: Any, db: Session
) -> tuple[uuid.UUID, dict[str, str], dict[str, str], str]:
    account, owner, operator = await operator_for(api, mail, db)
    tenant_id = uuid.UUID(str(account["tenant_id"]))
    profile = db.get(BusinessProfile, tenant_id)
    if profile is None:
        profile = BusinessProfile(tenant_id=tenant_id, business_name="Clínica")
        db.add(profile)
    profile.opening_hours = HOURS
    profile.timezone = "America/Lima"
    db.commit()
    created = await api.post(
        "/api/v1/tenant/products",
        headers=owner,
        json={
            "name": "Consulta",
            "price": "80",
            "agendable": True,
            "duracion_min": 60,
            "buffer_min": 0,
        },
    )
    assert created.status_code == 201, created.text
    return tenant_id, owner, operator, created.json()["id"]


async def _book(
    api: AsyncClient, headers: dict[str, str], service: str, day: str, at: str = "10:00"
) -> Any:
    return await api.post(
        A,
        headers=headers,
        json={"servicio_id": service, "inicio": f"{day}T{at}", "nombre_cliente": "Rosa Quispe"},
    )


class TestTheBookableService:
    async def test_a_service_is_bookable_only_with_a_duration(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _, service = await _shop(api, mail, db)

        refused = await api.post(
            "/api/v1/tenant/products",
            headers=owner,
            json={"name": "Visita", "price": "0", "agendable": True},
        )
        edited = await api.put(
            f"/api/v1/tenant/products/{service}", headers=owner, json={"duracion_min": 45}
        )

        assert refused.status_code == 400
        assert edited.json()["duracion_min"] == 45 and edited.json()["agendable"] is True


class TestTheSetup:
    async def test_the_overview_lists_what_can_be_booked_and_the_defaults(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _, service = await _shop(api, mail, db)

        overview = (await api.get(S, headers=owner)).json()

        assert overview["zona"] == "America/Lima"
        assert [s["id"] for s in overview["servicios"]] == [service]
        assert overview["ajustes"]["anticipacion_min_horas"] == 2
        assert overview["ajustes"]["recordatorio_24h"] is False

    async def test_the_owner_sets_up_a_resource_and_the_operator_cannot(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, operator, _ = await _shop(api, mail, db)
        body = {"nombre": "Consultorio 1", "horario": {"mon": [["09:00", "12:00"]]}}

        refused = await api.post(f"{S}/resources", headers=operator, json=body)
        created = await api.post(f"{S}/resources", headers=owner, json=body)
        bad = await api.post(
            f"{S}/resources",
            headers=owner,
            json={"nombre": "X", "horario": {"mon": [["12:00", "09:00"]]}},
        )

        assert refused.status_code == 403
        assert created.status_code == 201 and created.json()["horario"] == {
            "mon": [["09:00", "12:00"]]
        }
        assert bad.status_code == 400

    async def test_reminders_need_an_approved_template(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _, _ = await _shop(api, mail, db)

        refused = await api.put(f"{S}/settings", headers=owner, json={"recordatorio_24h": True})
        template = MessageTemplate(
            tenant_id=tenant_id,
            name="recordatorio_cita",
            label="Recordatorio de cita",
            category="utility",
            body="Hola {{nombre}}, te recordamos tu cita.",
            variables=["nombre"],
            status="approved",
        )
        db.add(template)
        db.commit()
        accepted = await api.put(
            f"{S}/settings",
            headers=owner,
            json={"recordatorio_24h": True, "plantilla_recordatorio_id": str(template.id)},
        )

        assert refused.status_code == 422 and refused.json()["error"] == "needs_template"
        assert accepted.status_code == 200 and accepted.json()["recordatorio_24h"] is True
        assert accepted.json()["plantilla_aprobada"] is True


class TestTheAgenda:
    async def test_a_walk_in_is_booked_listed_and_cannot_be_doubled(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, operator, service = await _shop(api, mail, db)
        day = _next_weekday()

        booked = await _book(api, operator, service, day)
        again = await _book(api, owner, service, day, "10:30")
        listed = (await api.get(A, headers=owner, params={"desde": day})).json()

        assert booked.status_code == 201
        assert (
            booked.json()["inicio_local"] == f"{day}T10:00"
            and booked.json()["cliente"] == "Rosa Quispe"
        )
        assert again.status_code == 409 and again.json()["error"] == "slot_taken"
        assert [i["id"] for i in listed["items"]] == [booked.json()["id"]]

    async def test_the_free_times_exclude_what_is_taken(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _, service = await _shop(api, mail, db)
        day = _next_weekday()
        await _book(api, owner, service, day)

        free = (
            await api.get(
                f"{S}/availability", headers=owner, params={"servicio_id": service, "desde": day}
            )
        ).json()["items"]

        starts = [i["inicio_local"] for i in free if i["inicio_local"].startswith(day)]
        assert f"{day}T10:00" not in starts and f"{day}T11:00" in starts

    async def test_confirm_move_and_cancel(self, api: AsyncClient, mail: Any, db: Session) -> None:
        _, owner, operator, service = await _shop(api, mail, db)
        day = _next_weekday()
        booked = (await _book(api, operator, service, day)).json()

        confirmed = await api.post(
            f"{A}/{booked['id']}/action", headers=operator, json={"accion": "confirmar"}
        )
        moved = await api.post(
            f"{A}/{booked['id']}/reschedule", headers=operator, json={"inicio": f"{day}T16:00"}
        )
        cancelled = await api.post(
            f"{A}/{booked['id']}/action",
            headers=owner,
            json={"accion": "cancelar", "motivo": "llamó"},
        )
        again = await api.post(
            f"{A}/{booked['id']}/action", headers=owner, json={"accion": "confirmar"}
        )

        assert confirmed.json()["status"] == "confirmada"
        assert (
            moved.json()["inicio_local"] == f"{day}T16:00" and moved.json()["status"] == "agendada"
        )
        assert cancelled.json()["status"] == "cancelada"
        assert again.status_code == 422 and again.json()["error"] == "not_live"

    async def test_moving_onto_another_appointment_is_a_conflict(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _, service = await _shop(api, mail, db)
        day = _next_weekday()
        await _book(api, owner, service, day, "11:00")
        mine = (await _book(api, owner, service, day, "10:00")).json()

        clash = await api.post(
            f"{A}/{mine['id']}/reschedule", headers=owner, json={"inicio": f"{day}T11:00"}
        )

        assert clash.status_code == 409

    async def test_the_rules_apply_to_a_person_too(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _, service = await _shop(api, mail, db)

        too_far = await _book(api, owner, service, _next_weekday(days_ahead=90))
        night = await _book(api, owner, service, _next_weekday(), "23:00")

        assert too_far.status_code == 422 and too_far.json()["error"] == "too_far"
        assert night.status_code == 409  # closed: no resource is open then

    async def test_another_business_cannot_see_or_touch_it(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, owner, _, service = await _shop(api, mail, db)
        _, stranger, _, _ = await _shop(api, mail, db)
        day = _next_weekday()
        booked = (await _book(api, owner, service, day)).json()

        listed = (await api.get(A, headers=stranger, params={"desde": day})).json()
        touched = await api.post(
            f"{A}/{booked['id']}/action", headers=stranger, json={"accion": "cancelar"}
        )

        assert listed["items"] == []
        assert touched.status_code == 404
