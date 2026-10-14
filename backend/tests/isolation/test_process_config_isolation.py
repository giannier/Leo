"""The effective process configuration through the real database
(Feature 004, TASK-414).

`ProcessConfigService` reads six tenant-scoped tables and caches the result per
tenant. Two things can go wrong that only two real tenants can show: one business
reading another's template, options or promotions, and the cache
handing one tenant's configuration to the other.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.process.service import (
    CACHE_TTL_SECONDS,
    DEFAULT_CACHE,
    ProcessConfigCache,
    ProcessConfigService,
)
from app.process.templates.base import Stage
from app.repositories.business import AgentConfigRepository, BusinessProfileRepository
from app.repositories.payment import PaymentMethodRepository
from app.repositories.sales_config import BusinessInfoRepository, PromotionRepository
from tests.isolation.conftest import TenantFixture

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _service(db: Session, cache: ProcessConfigCache | None = None) -> ProcessConfigService:
    return ProcessConfigService(db, cache=cache or ProcessConfigCache(), now=lambda: NOW)


def _promotion(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "nombre": "10 % de descuento",
        "tipo": "porcentaje",
        "valor": Decimal("10.00"),
    }
    values.update(overrides)
    return values


def _configure(db: Session, fixture: TenantFixture, label: str) -> None:
    """Give a tenant a configuration that differs from the other's in every part."""
    tenant = fixture.tenant_id
    cert = label == "A"
    AgentConfigRepository(db, tenant).upsert(
        process_template_key="certificaciones" if cert else "servicios",
        process_options=(
            {"datos_requeridos": ["dni", "correo"], "variables": {"marca": "Capacita"}}
            if cert
            else {"datos_requeridos": ["nombres"]}
        ),
        agent_name=f"Asistente {label}",
        ritmo="directo" if cert else "paso_a_paso",
        detalle="breve" if cert else "extenso",
        saludo=f"Saludo de {label}",
    )
    BusinessProfileRepository(db, tenant).upsert(
        business_name=f"Negocio {label}",
        industry="instituto",
        currency="PEN" if cert else "USD",
        timezone="America/Lima",
    )
    BusinessInfoRepository(db, tenant).upsert(
        "entregas", {"hace_delivery": cert, "zonas": [], "recojo_en_tienda": not cert}
    )
    PaymentMethodRepository(db, tenant).add(method="yape" if cert else "plin", instructions="x")
    PromotionRepository(db, tenant).add(**_promotion(nombre=f"Promo {label}"))


@pytest.fixture
def configured(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> None:
    _configure(db, tenant_a, "A")
    _configure(db, tenant_b, "B")


class TestEachTenantReadsItsOwnConfiguration:
    def test_template_options_and_behaviour(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, configured: None
    ) -> None:
        service = _service(db)

        a = service.effective(tenant_a.tenant_id)
        b = service.effective(tenant_b.tenant_id)

        assert (a.template.key, b.template.key) == ("certificaciones", "servicios")
        assert a.options.datos_requeridos == ("dni", "correo")
        assert b.options.datos_requeridos == ("nombres",)
        assert a.options.variables == {"marca": "Capacita"}
        assert (a.behavior.agent_name, b.behavior.agent_name) == ("Asistente A", "Asistente B")
        assert (a.behavior.ritmo, b.behavior.ritmo) == ("directo", "paso_a_paso")
        assert (a.behavior.detalle, b.behavior.detalle) == ("breve", "extenso")
        assert (a.behavior.saludo, b.behavior.saludo) == ("Saludo de A", "Saludo de B")
        assert (a.business_name, b.business_name) == ("Negocio A", "Negocio B")
        assert (a.currency, b.currency) == ("PEN", "USD")

    def test_stage_texts_business_info_methods_and_promotions(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, configured: None
    ) -> None:
        service = _service(db)

        a = service.effective(tenant_a.tenant_id)
        b = service.effective(tenant_b.tenant_id)

        # Each reads the published rules of its own template (017, TASK-460).
        assert a.instructions_for(Stage.IDENTIFICAR).startswith("Paso 1 · El tema.")
        assert b.instructions_for(Stage.IDENTIFICAR).startswith("Entiende qué necesita")
        assert (a.process_version, b.process_version) == (1, 1)
        assert a.info.entregas is not None and a.info.entregas.hace_delivery is True
        assert b.info.entregas is not None and b.info.entregas.hace_delivery is False
        assert (a.payment_methods, b.payment_methods) == (("yape",), ("plin",))
        assert [p.nombre for p in a.promotions] == ["Promo A"]
        assert [p.nombre for p in b.promotions] == ["Promo B"]

    def test_one_shared_cache_never_crosses_tenants(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, configured: None
    ) -> None:
        cache = ProcessConfigCache()
        service = _service(db, cache)

        first_a = service.effective(tenant_a.tenant_id)
        first_b = service.effective(tenant_b.tenant_id)

        assert service.effective(tenant_a.tenant_id) is first_a
        assert service.effective(tenant_b.tenant_id) is first_b
        assert first_a.tenant_id == tenant_a.tenant_id
        assert first_b.tenant_id == tenant_b.tenant_id
        assert first_a.business_name == "Negocio A"


class TestDefaults:
    def test_a_tenant_with_no_configuration_runs_the_default_template(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        config = _service(db).effective(tenant_a.tenant_id)

        assert config.template.key == "productos"
        assert config.options.modalidad_pago == "previo"
        assert config.options.datos_requeridos == ("nombres", "telefono")
        assert config.behavior.agent_name == "Leo"
        assert (config.behavior.tone, config.behavior.ritmo, config.behavior.detalle) == (
            "cercano",
            "equilibrado",
            "moderado",
        )
        assert config.process_version == 1
        assert config.currency == "PEN"
        assert config.business_name is None
        assert config.payment_methods == ()
        assert config.promotions == ()
        for stage in Stage:
            assert config.instructions_for(stage) == config.template.stage(stage).instrucciones

    def test_options_that_do_not_fit_the_template_fall_back_to_its_defaults(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        AgentConfigRepository(db, tenant_a.tenant_id).upsert(
            process_template_key="certificaciones",
            process_options={"modalidad_pago": "contra_entrega"},
        )

        config = _service(db).effective(tenant_a.tenant_id)

        assert config.options.modalidad_pago == "previo"
        assert config.options.datos_requeridos == (
            "dni",
            "nombres",
            "apellidos",
            "correo",
            "telefono",
        )

    def test_an_unknown_template_key_runs_the_default_template(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        AgentConfigRepository(db, tenant_a.tenant_id).upsert(process_template_key="ya_no_existe")

        assert _service(db).effective(tenant_a.tenant_id).template.key == "productos"

    def test_an_invalid_stored_section_is_skipped(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        sections = BusinessInfoRepository(db, tenant_a.tenant_id)
        sections.upsert("entregas", {"hace_delivery": "quizá"})
        sections.upsert("devoluciones", {"acepta_cambios": True, "acepta_devoluciones": False})

        info = _service(db).effective(tenant_a.tenant_id).info

        assert info.entregas is None
        assert info.devoluciones is not None


class TestPromotionsAndFactsConfig:
    def test_only_promotions_in_force_are_loaded(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        promotions = PromotionRepository(db, tenant_a.tenant_id)
        promotions.add(**_promotion(nombre="Vigente"))
        promotions.add(**_promotion(nombre="Vencida", termina_en=NOW - timedelta(days=1)))
        promotions.add(**_promotion(nombre="Futura", inicia_en=NOW + timedelta(days=1)))
        promotions.add(**_promotion(nombre="Apagada", is_active=False))

        config = _service(db).effective(tenant_a.tenant_id)

        assert [p.nombre for p in config.promotions] == ["Vigente"]

    def test_the_promotions_survive_the_session(self, db: Session, tenant_a: TenantFixture) -> None:
        PromotionRepository(db, tenant_a.tenant_id).add(
            **_promotion(uso="cierre", vigencia_horas=24, nombre="Cupón")
        )
        config = _service(db).effective(tenant_a.tenant_id)

        db.expunge_all()

        (promotion,) = config.promotions
        assert (promotion.nombre, promotion.uso, promotion.vigencia_horas) == (
            "Cupón",
            "cierre",
            24,
        )

    def test_the_facts_config_carries_the_options_deliveries_and_promotions(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        BusinessInfoRepository(db, tenant_a.tenant_id).upsert(
            "entregas", {"hace_delivery": True, "zonas": [], "recojo_en_tienda": False}
        )
        PromotionRepository(db, tenant_a.tenant_id).add(**_promotion())
        config = _service(db).effective(tenant_a.tenant_id)

        facts_config = config.facts_config()

        assert facts_config.options is config.options
        assert facts_config.entregas is config.info.entregas
        assert len(facts_config.promotions) == 1


class TestTheCache:
    def _count(self, engine: Engine, run: Any) -> int:
        statements: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            run()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    def test_a_second_read_within_a_minute_costs_no_query(
        self, engine: Engine, db: Session, tenant_a: TenantFixture
    ) -> None:
        service = _service(db)
        first = service.effective(tenant_a.tenant_id)

        queries = self._count(engine, lambda: service.effective(tenant_a.tenant_id))

        assert queries == 0
        assert service.effective(tenant_a.tenant_id) is first

    def test_the_first_read_takes_eight_statements(
        self, engine: Engine, db: Session, tenant_a: TenantFixture
    ) -> None:
        # Agent, profile, the published process version, the business's switches
        # and the process rules (017), business info, payment methods and
        # promotions. A business with a rubro adds its version and its rules.
        service = _service(db)

        assert self._count(engine, lambda: service.effective(tenant_a.tenant_id)) == 8

    def test_an_edit_is_seen_after_the_cache_expires(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        clock = _Clock()
        service = _service(db, ProcessConfigCache(clock=clock))
        AgentConfigRepository(db, tenant_a.tenant_id).upsert(agent_name="Antes")
        assert service.effective(tenant_a.tenant_id).behavior.agent_name == "Antes"

        AgentConfigRepository(db, tenant_a.tenant_id).upsert(agent_name="Después")
        clock.now += CACHE_TTL_SECONDS - 1
        assert service.effective(tenant_a.tenant_id).behavior.agent_name == "Antes"

        clock.now += 1
        assert service.effective(tenant_a.tenant_id).behavior.agent_name == "Después"

    def test_invalidating_forgets_only_that_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture, configured: None
    ) -> None:
        service = _service(db)
        a = service.effective(tenant_a.tenant_id)
        b = service.effective(tenant_b.tenant_id)
        AgentConfigRepository(db, tenant_a.tenant_id).upsert(agent_name="Nuevo nombre")

        service.invalidate(tenant_a.tenant_id)

        assert service.effective(tenant_a.tenant_id).behavior.agent_name == "Nuevo nombre"
        assert service.effective(tenant_a.tenant_id) is not a
        assert service.effective(tenant_b.tenant_id) is b

    def test_the_cache_can_be_cleared(self, db: Session, tenant_a: TenantFixture) -> None:
        cache = ProcessConfigCache()
        service = _service(db, cache)
        first = service.effective(tenant_a.tenant_id)

        cache.clear()

        assert service.effective(tenant_a.tenant_id) is not first

    def test_services_built_without_a_cache_share_the_worker_cache(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        DEFAULT_CACHE.clear()
        try:
            first = ProcessConfigService(db).effective(tenant_a.tenant_id)

            assert ProcessConfigService(db).effective(tenant_a.tenant_id) is first
        finally:
            DEFAULT_CACHE.clear()
