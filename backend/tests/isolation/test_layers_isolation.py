"""Rule switches and catalogue news are per business (017, TASK-461).

Two businesses on the same template and rubro: a rule one of them switches off
must stay on for the other, and one reviewer marking news as seen must not
silence the other's.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.process.layers import LayerResolver
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.templates.base import Stage
from app.repositories.base import TenantScopeError
from app.repositories.business import BusinessProfileRepository
from app.repositories.catalog import CatalogRepository
from app.repositories.layers import TenantCatalogSeenRepository, TenantRuleSwitchRepository
from tests.isolation.conftest import TenantFixture

RULE = "COBRO.pedido_datos_completos"


def _config(db: Session, fixture: TenantFixture):  # type: ignore[no-untyped-def]
    return ProcessConfigService(db, cache=ProcessConfigCache()).effective(fixture.tenant_id)


class TestSwitches:
    def test_a_rule_off_for_one_business_stays_on_for_the_other(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        LayerResolver(db, tenant_a.tenant_id).switch(
            "proceso", "productos", RULE, apagada=True, by=None
        )

        assert "Pedido y datos completos." not in _config(db, tenant_a).instructions_for(
            Stage.COBRO
        )
        assert "Pedido y datos completos." in _config(db, tenant_b).instructions_for(Stage.COBRO)
        assert TenantRuleSwitchRepository(db, tenant_b.tenant_id).off_keys("proceso") == set()

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            TenantRuleSwitchRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, kind="proceso", rule_key=RULE, apagada=True
            )

    def test_the_panel_list_shows_only_your_own_switches(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        LayerResolver(db, tenant_a.tenant_id).switch(
            "proceso", "productos", RULE, apagada=True, by=None
        )

        mine = {
            r.rule_key: r.apagada
            for r in LayerResolver(db, tenant_a.tenant_id).rules("proceso", "productos")
        }
        theirs = {
            r.rule_key: r.apagada
            for r in LayerResolver(db, tenant_b.tenant_id).rules("proceso", "productos")
        }
        assert mine[RULE] is True and theirs[RULE] is False


class TestNews:
    def test_marking_seen_is_per_business(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        for fixture in (tenant_a, tenant_b):
            BusinessProfileRepository(db, fixture.tenant_id).upsert(industry="retail")
            assert LayerResolver(db, fixture.tenant_id).news("rubro", "retail") is None
        catalog = CatalogRepository(db)
        draft = catalog.new_draft("rubro", "retail")
        from app.models.catalog import CatalogRule

        db.add(CatalogRule(version_id=draft.id, orden=99, rule_key="rubro.nueva", texto="Nueva"))
        db.flush()
        catalog.publish(draft, by=None)

        LayerResolver(db, tenant_a.tenant_id).mark_seen("rubro", "retail")

        assert LayerResolver(db, tenant_a.tenant_id).news("rubro", "retail") is None
        news = LayerResolver(db, tenant_b.tenant_id).news("rubro", "retail")
        assert news is not None and [r.rule_key for r in news.rules] == ["rubro.nueva"]
        assert TenantCatalogSeenRepository(db, tenant_b.tenant_id).seen("rubro", "retail") == 1
