"""A hundred people ask for the same minute: exactly the places there are get booked (014, 1408).

Real, separate database sessions in real threads, committed: the exclusion constraint is the last
word, not the code that checked first. Everything the test creates is deleted afterwards.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.models.business import BusinessProfile, Product
from app.models.scheduling import Resource
from app.models.tenant import Tenant
from app.scheduling.service import Scheduling, SchedulingError, SlotTaken

LIMA = ZoneInfo("America/Lima")
TODAY = datetime(2026, 10, 26, tzinfo=LIMA).date()  # a Monday
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=LIMA).astimezone(UTC)
HOURS = {d: [["09:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
TABLES = (
    "appointment_events",
    "appointments",
    "resource_blocks",
    "resources",
    "scheduling_settings",
    "products",
    "business_profiles",
    "tenants",
)


def at(hour: int) -> datetime:
    return datetime(TODAY.year, TODAY.month, TODAY.day + 1, hour, tzinfo=LIMA).astimezone(UTC)


@pytest.fixture
def world(engine: Engine):  # type: ignore[no-untyped-def]
    """A committed business with a bookable service and `resources` resources; removed after."""
    created: list[uuid.UUID] = []

    def make(resources: int) -> tuple[uuid.UUID, uuid.UUID]:
        with Session(engine) as session:
            tenant = Tenant(
                name=f"Concurrencia {uuid.uuid4().hex[:6]}",
                status="trial",
                plan_id="trial",
                trial_ends_at=NOW + timedelta(days=7),
            )
            session.add(tenant)
            session.flush()
            session.add(
                BusinessProfile(
                    tenant_id=tenant.id,
                    business_name="Clínica",
                    opening_hours=HOURS,
                    timezone="America/Lima",
                )
            )
            product = Product(
                tenant_id=tenant.id,
                name="Consulta",
                price=Decimal("80"),
                agendable=True,
                duracion_min=60,
            )
            session.add(product)
            for n in range(resources):
                session.add(Resource(tenant_id=tenant.id, nombre=f"Consultorio {n + 1}"))
            session.commit()
            created.append(tenant.id)
            return tenant.id, product.id

    yield make
    with engine.begin() as connection:
        for tenant_id in created:
            for table in TABLES:
                column = "id" if table == "tenants" else "tenant_id"
                connection.execute(
                    text(f"DELETE FROM {table} WHERE {column} = :t"), {"t": tenant_id}
                )


def attempt(engine: Engine, tenant_id: uuid.UUID, product_id: uuid.UUID, hour: int) -> str:
    """One customer asking for `hour` tomorrow, in a session of their own."""
    with Session(engine) as session:
        scheduling = Scheduling(session, tenant_id, now=NOW)
        product = session.get(Product, product_id)
        assert product is not None
        try:
            scheduling.book(product, at(hour), by="agent")
            session.commit()
            return "booked"
        except SlotTaken:
            session.rollback()
            return "taken"
        except SchedulingError as exc:  # pragma: no cover - a rule, not a race
            session.rollback()
            return exc.code


class TestAHundredAtOnce:
    def test_one_place_goes_to_one_person(self, engine: Engine, world) -> None:  # type: ignore[no-untyped-def]
        tenant_id, product_id = world(resources=1)

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(
                pool.map(lambda _: attempt(engine, tenant_id, product_id, 10), range(100))
            )

        assert results.count("booked") == 1 and results.count("taken") == 99

    def test_two_resources_and_five_hours_take_exactly_ten(self, engine: Engine, world) -> None:  # type: ignore[no-untyped-def]
        tenant_id, product_id = world(resources=2)
        hours = [9, 10, 11, 12, 13]

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(
                pool.map(lambda i: attempt(engine, tenant_id, product_id, hours[i % 5]), range(100))
            )

        assert results.count("booked") == 10 and results.count("taken") == 90
        with Session(engine) as session:
            rows = session.execute(
                text("SELECT starts_at, resource_id FROM appointments WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).all()
        assert len({(r.starts_at, r.resource_id) for r in rows}) == 10  # nobody shares a place
