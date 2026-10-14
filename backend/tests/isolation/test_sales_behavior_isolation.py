"""Sales behaviour and plan levels (Feature 004, TASK-404).

Stage overrides were removed by the 017 (C-01): stages are no longer edited. The agent
configuration is a different risk: the new columns must have defaults for
tenants that existed before the migration and must be enforced by the database
for writers that skip the API.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.plan import Plan
from app.repositories.business import AgentConfigRepository
from tests.isolation.conftest import TenantFixture


@pytest.fixture
def seeded(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    for label, fixture in (("A", tenant_a), ("B", tenant_b)):
        AgentConfigRepository(db, fixture.tenant_id).upsert(
            ritmo="directo" if label == "A" else "paso_a_paso"
        )


class TestAgentBehaviorIsPerTenant:
    def test_each_tenant_reads_its_own_pace(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, seeded: None
    ) -> None:
        mine = AgentConfigRepository(db, tenant_a.tenant_id).current()
        theirs = AgentConfigRepository(db, tenant_b.tenant_id).current()

        assert mine is not None and theirs is not None
        assert (mine.ritmo, theirs.ritmo) == ("directo", "paso_a_paso")

    def test_a_new_configuration_starts_on_the_defaults(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        config = AgentConfigRepository(db, tenant_a.tenant_id).upsert(agent_name="Leo")
        db.refresh(config)

        assert config.ritmo == "equilibrado"
        assert config.detalle == "moderado"
        assert config.seguimiento_activo is False
        assert (config.seguimiento_horas, config.seguimiento_max) == (4, 2)
        assert config.nivel_inteligencia == "economico"
        assert config.derivacion_pausa_horas == 6
        assert config.process_template_key == "productos"
        assert config.process_options == {}
        assert config.system_messages == {}

    def test_a_row_written_before_the_migration_gets_the_defaults(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """An INSERT that names none of the new columns is what every existing
        tenant's row looks like to the database."""
        db.execute(
            text("INSERT INTO agent_configs (tenant_id) VALUES (:tenant_id)"),
            {"tenant_id": tenant_a.tenant_id},
        )
        config = AgentConfigRepository(db, tenant_a.tenant_id).current()

        assert config is not None
        assert (config.process_template_key, config.ritmo, config.detalle) == (
            "productos",
            "equilibrado",
            "moderado",
        )
        assert config.nivel_inteligencia == "economico"

    def test_the_json_columns_round_trip(self, db: Session, tenant_a: TenantFixture) -> None:
        options = {"modalidad_pago": "previo", "variables": {"tema": "inglés"}}
        config = AgentConfigRepository(db, tenant_a.tenant_id).upsert(
            process_options=options, system_messages={"saludo": "Hola"}
        )
        db.refresh(config)

        assert config.process_options == options
        assert config.system_messages == {"saludo": "Hola"}


class TestBehaviorRulesLiveInTheDatabase:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"ritmo": "inventado"},
            {"detalle": "inventado"},
            {"nivel_inteligencia": "inventado"},
            {"seguimiento_horas": 0},
            {"seguimiento_horas": 21},
            {"seguimiento_max": 0},
            {"seguimiento_max": 4},
            {"derivacion_pausa_horas": 0},
            {"derivacion_pausa_horas": 73},
        ],
    )
    def test_an_invalid_value_is_rejected(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            AgentConfigRepository(db, tenant_a.tenant_id).upsert(**overrides)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"ritmo": "directo", "detalle": "breve", "nivel_inteligencia": "mejor"},
            {"ritmo": "paso_a_paso", "detalle": "extenso", "nivel_inteligencia": "medio"},
            {"seguimiento_horas": 1, "seguimiento_max": 1, "derivacion_pausa_horas": 1},
            {"seguimiento_horas": 20, "seguimiento_max": 3, "derivacion_pausa_horas": 72},
        ],
    )
    def test_the_limits_themselves_are_accepted(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        config = AgentConfigRepository(db, tenant_a.tenant_id).upsert(**overrides)

        for field, value in overrides.items():
            assert getattr(config, field) == value


class TestPlanIntelligenceLevels:
    def test_every_existing_plan_allows_the_economic_level(self, db: Session) -> None:
        plans = db.query(Plan).all()

        assert plans
        assert all("economico" in plan.allowed_intelligence for plan in plans)

    def test_paid_plans_allow_every_level_and_the_trial_only_the_economic_one(
        self, db: Session
    ) -> None:
        """Milestone B, decision 4: levels are not locked by plan (migration 0027)."""
        levels = {plan.id: plan.allowed_intelligence for plan in db.query(Plan).all()}

        assert levels.pop("trial") == ["economico"]
        assert levels
        assert all(allowed == ["economico", "medio", "mejor"] for allowed in levels.values())

    @pytest.mark.parametrize("levels", [[], ["inventado"], ["economico", "inventado"]])
    def test_an_empty_or_unknown_list_is_rejected(self, db: Session, levels: list[str]) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Plan(
                    id="prueba",
                    name="Prueba",
                    max_seats=1,
                    max_ai_messages=1,
                    max_rag_chars=1,
                    max_knowledge_files=1,
                    allowed_intelligence=levels,
                )
            )
            db.flush()

    def test_a_plan_can_allow_several_levels(self, db: Session) -> None:
        plan = Plan(
            id="prueba",
            name="Prueba",
            max_seats=1,
            max_ai_messages=1,
            max_rag_chars=1,
            max_knowledge_files=1,
            allowed_intelligence=["economico", "medio", "mejor"],
        )
        db.add(plan)
        db.flush()
        db.refresh(plan)

        assert plan.allowed_intelligence == ["economico", "medio", "mejor"]
