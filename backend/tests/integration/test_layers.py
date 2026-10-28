"""Layers resolved from the catalogue, switches, news and rubro knowledge (017, TASK-461)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.agent.tools.catalog import consultar_conocimiento
from app.agent.tools.registry import ToolContext
from app.models.business import BusinessProfile
from app.models.catalog import CatalogEmbedding, CatalogRule
from app.models.tenant import Tenant, TenantUser
from app.process.layers import LayerResolver, UnknownRule
from app.process.service import DEFAULT_CACHE, ProcessConfigCache, ProcessConfigService
from app.process.templates.base import Stage
from app.providers.llm.offline import OfflineLLMProvider
from app.repositories.catalog import CatalogRepository
from app.services.catalog_knowledge import index_published_rubros, search_rubro


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name=f"Capas {uuid.uuid4().hex[:6]}")
    db.add(row)
    db.flush()
    db.add(BusinessProfile(tenant_id=row.id, business_name="Capas", industry="instituto"))
    db.flush()
    return row


def _config(db: Session, tenant: Tenant):  # type: ignore[no-untyped-def]
    return ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)


class TestResolution:
    def test_the_rubro_rules_reach_the_configuration(self, db: Session, tenant: Tenant) -> None:
        config = _config(db, tenant)

        # Version 2: the education template of feature 020 (migration 0073).
        assert config.rubro_version == 2
        assert any("asesor académico" in rule for rule in config.rubro_rules)

    def test_a_rubro_rule_switched_off_is_gone(self, db: Session, tenant: Tenant) -> None:
        LayerResolver(db, tenant.id).switch(
            "rubro", "instituto", "rubro.tono_asesor", apagada=True, by=None
        )

        assert not any("asesor académico" in r for r in _config(db, tenant).rubro_rules)

    def test_switching_every_rule_of_a_stage_off_leaves_it_empty(
        self, db: Session, tenant: Tenant
    ) -> None:
        resolver = LayerResolver(db, tenant.id)
        for rule in resolver.rules("proceso", "productos"):
            if rule.etapa == "DATOS":
                resolver.switch("proceso", "productos", rule.rule_key, apagada=True, by=None)

        # Not the code text: the business switched them off on purpose.
        assert _config(db, tenant).instructions_for(Stage.DATOS) == ""

    def test_switching_back_on_restores_it(self, db: Session, tenant: Tenant) -> None:
        resolver = LayerResolver(db, tenant.id)
        key = "DATOS.pedido_esta_completo_pide_datos"
        resolver.switch("proceso", "productos", key, apagada=True, by=None)
        resolver.switch("proceso", "productos", key, apagada=False, by=None)

        assert _config(db, tenant).instructions_for(Stage.DATOS).startswith("El pedido está")

    def test_a_switch_invalidates_the_shared_cache(self, db: Session, tenant: Tenant) -> None:
        ProcessConfigService(db).effective(tenant.id)
        assert DEFAULT_CACHE.get(tenant.id) is not None

        LayerResolver(db, tenant.id).switch(
            "rubro", "instituto", "rubro.vocabulario", apagada=True, by=None
        )

        assert DEFAULT_CACHE.get(tenant.id) is None

    def test_an_unknown_rule_is_refused(self, db: Session, tenant: Tenant) -> None:
        with pytest.raises(UnknownRule):
            LayerResolver(db, tenant.id).switch(
                "rubro", "instituto", "rubro.no_existe", apagada=True, by=None
            )


class TestNewVersions:
    def _publish_with(self, db: Session, *rules: tuple[str, str]) -> None:
        catalog = CatalogRepository(db)
        draft = catalog.new_draft("rubro", "instituto")
        for i, (key, text) in enumerate(rules, start=100):
            db.add(CatalogRule(version_id=draft.id, orden=i, rule_key=key, texto=text))
        db.flush()
        catalog.publish(draft, by=None)

    def test_new_rules_arrive_on_and_a_switched_off_key_stays_off(
        self, db: Session, tenant: Tenant
    ) -> None:
        resolver = LayerResolver(db, tenant.id)
        assert resolver.news("rubro", "instituto") is None  # starts at the published v2
        resolver.switch("rubro", "instituto", "rubro.vocabulario", apagada=True, by=None)

        self._publish_with(db, ("rubro.nueva", "Regla nueva del rubro."))
        rules = _config(db, tenant).rubro_rules

        assert "Regla nueva del rubro." in rules
        assert not any("evita" in r for r in rules)
        news = resolver.news("rubro", "instituto")
        assert news is not None and (news.seen, news.version) == (2, 3)
        assert [r.rule_key for r in news.rules] == ["rubro.nueva"]

        resolver.mark_seen("rubro", "instituto")
        assert resolver.news("rubro", "instituto") is None


class TestReviewer:
    def test_the_owner_always_reviews_and_others_only_with_the_permission(self) -> None:
        assert TenantUser(role="owner", puede_revisar=False).can_review
        assert not TenantUser(role="operator", puede_revisar=False).can_review
        assert TenantUser(role="operator", puede_revisar=True).can_review


class TestRubroKnowledge:
    async def test_the_rubro_faq_is_found_and_labelled_as_guidance(
        self, db: Session, tenant: Tenant
    ) -> None:
        llm = OfflineLLMProvider(environment="test")
        # A dev worker may already have embedded the published rubros in this
        # database; start from none (rolled back with the test).
        db.execute(
            delete(CatalogEmbedding).where(CatalogEmbedding.embedding_model == llm.embedding_model)
        )
        assert await index_published_rubros(db, llm) > 0
        assert await index_published_rubros(db, llm) == 0  # already done
        config = _config(db, tenant)
        context = ToolContext(
            session=db, tenant_id=tenant.id, llm=llm, rubro_version_id=config.rubro_version_id
        )

        result = await consultar_conocimiento(
            context, {"consulta": "¿El certificado tiene validez? Responde solo con lo que diga"}
        )

        assert result.status == "hecho"
        assert "Guía del rubro" in (result.texto or "")
        assert "validez" in (result.texto or "")

    async def test_nothing_unrelated_and_nothing_with_another_model(
        self, db: Session, tenant: Tenant
    ) -> None:
        llm = OfflineLLMProvider(environment="test")
        await index_published_rubros(db, llm)
        version_id = _config(db, tenant).rubro_version_id
        vector = (await llm.embed(["zzz qqq xxx"], input_type="query"))[0]

        assert search_rubro(db, version_id, vector, embedding_model=llm.embedding_model) == []
        same = (await llm.embed(["¿El certificado tiene validez?"]))[0]
        assert search_rubro(db, version_id, same, embedding_model="otro-modelo") == []
