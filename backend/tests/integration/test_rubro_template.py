"""The template of a rubro, applied by a business that then owns it (feature 020, phase F3).

Giannier, 2026-10-28: «le damos una opción inicial y ellos pueden ir cambiando, mejorando o
quitando». The education template (migration 0073) is the one these tests apply: nine suggested
situations (three need a value from the business), two paths, the customer data and the payment.
No OpenAI call.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue
from app.learning.history import ConfigHistoryService
from app.models.business import AgentConfig, BusinessProfile
from app.models.catalog import CatalogSuggestedSituation
from app.models.learning import ConfigHistory
from app.models.situation import Situation
from app.models.tenant import Tenant
from app.process.layers import LayerResolver, UnknownRule
from app.process.service import DEFAULT_CACHE, ProcessConfigCache, ProcessConfigService
from app.process.templates import UNIVERSAL
from app.repositories.catalog import CatalogRepository
from app.services.rubro_template import RubroTemplateService, TemplateError, key_from_title
from app.services.situations import EMBED_JOB, SituationInput, SituationService
from tests.integration.test_assistant_config_api import _account, _role
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.integration.test_turn_pipeline import _tenant

A = "/api/v1/assistant"
TENANT = "/api/v1/tenant"
WITH_FIELDS = {"educacion.factura", "educacion.grupo", "educacion.malla"}
GROUP = "educacion.grupo"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recording
    return recording


def _mine(db: Session, tenant_id: uuid.UUID) -> list[Situation]:
    return list(
        db.execute(select(Situation).where(Situation.tenant_id == tenant_id)).scalars().all()
    )


def _agent(db: Session, tenant_id: uuid.UUID) -> AgentConfig:
    agent = db.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)).scalar_one()
    db.refresh(agent)
    return agent


def _rubro_rules(db: Session, tenant: Tenant) -> tuple[str, ...]:
    DEFAULT_CACHE.invalidate(tenant.id)
    return ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id).rubro_rules


def _publish_v3(db: Session) -> None:
    """Zentek360 improves the group situation and adds one about scholarships."""
    catalog = CatalogRepository(db)
    draft = catalog.new_draft("rubro", "instituto", notas="v3 de prueba")
    rows = list(catalog.suggested_situations(draft.id))
    group = next(row for row in rows if row.clave == GROUP)
    group.que_hace = "Pregunta cuántas personas son y ofrece el precio de grupo con su descuento."
    db.add(
        CatalogSuggestedSituation(
            version_id=draft.id,
            orden=len(rows) + 1,
            clave="educacion.becas",
            titulo="Si preguntan por becas",
            cuando_aplica="El cliente pregunta si hay becas o medias becas.",
            que_hace="Dile que no hay becas y ofrécele el pago en cuotas si existe.",
            campos={},
        )
    )
    db.flush()
    catalog.publish(draft, by=None)


class TestTheEducationTemplate:
    def test_version_two_keeps_version_one_and_brings_the_process(self, db: Session) -> None:
        catalog = CatalogRepository(db)
        v1 = catalog.version("rubro", "instituto", 1)
        v2 = catalog.published("rubro", "instituto")
        assert v1 is not None and v2 is not None and v2.version == 2

        assert [(r.rule_key, r.texto) for r in catalog.rules(v2.id)] == [
            (r.rule_key, r.texto) for r in catalog.rules(v1.id)
        ]
        assert len(catalog.faqs(v2.id)) == len(catalog.faqs(v1.id))
        items = catalog.suggested_situations(v2.id)
        assert len(items) == 9 and all(item.clave for item in items)
        assert {item.clave for item in items if item.campos} == WITH_FIELDS
        plantilla = v2.plantilla or {}
        assert [c["clave"] for c in plantilla["calificacion"]["caminos"]] == [
            "certificacion",
            "programa",
        ]
        assert plantilla["datos_requeridos"] == [
            "dni",
            "nombres",
            "apellidos",
            "correo",
            "telefono",
        ]

    def test_a_new_draft_carries_the_keys_and_the_process(self, db: Session) -> None:
        catalog = CatalogRepository(db)

        draft = catalog.new_draft("rubro", "instituto")

        published = catalog.published("rubro", "instituto")
        assert published is not None
        assert draft.plantilla == published.plantilla
        assert [s.clave for s in catalog.suggested_situations(draft.id)] == [
            s.clave for s in catalog.suggested_situations(published.id)
        ]


class TestTheBookingRules:
    """Migration 0074 (T2.7): seven rubros told the assistant to hand every booking to a person,
    above the AGENDA of a business that has one."""

    RUBROS = ("automotriz", "belleza", "hogar", "inmobiliaria", "salud", "servicios", "turismo")

    @pytest.mark.parametrize("key", RUBROS)
    def test_the_new_version_books_with_the_agenda_and_keeps_the_rest(
        self, db: Session, key: str
    ) -> None:
        catalog = CatalogRepository(db)
        new = catalog.published("rubro", key)
        assert new is not None
        old = catalog.version("rubro", key, new.version - 1)
        assert old is not None

        before = {r.rule_key: r.texto for r in catalog.rules(old.id)}
        after = {r.rule_key: r.texto for r in catalog.rules(new.id)}
        changed = [k for k in after if after[k] != before.get(k)]

        assert list(after) == list(before) and len(changed) == 1
        assert "tienes agenda (bloque AGENDA)" in after[changed[0]]
        assert [f.pregunta for f in catalog.faqs(new.id)] == [
            f.pregunta for f in catalog.faqs(old.id)
        ]
        assert [s.titulo for s in catalog.suggested_situations(new.id)] == [
            s.titulo for s in catalog.suggested_situations(old.id)
        ]


class TestApplying:
    def test_the_greeting_always_enters_but_only_on_the_first_message(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        RubroTemplateService(db, tenant.id).apply(by=None)

        greeting = next(
            s for s in _mine(db, tenant.id) if s.plantilla_clave == "educacion.nombre_primero"
        )
        assert (greeting.siempre, greeting.etapas) == (True, ["INICIO"])
        others = [s for s in _mine(db, tenant.id) if s.id != greeting.id]
        assert not any(s.siempre or s.etapas for s in others)

    def test_it_brings_every_situation_and_the_process_the_template_suggests(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        applied = RubroTemplateService(db, tenant.id).apply(by=None)

        assert (applied.version, applied.activas, applied.borradores, applied.ya_estaban) == (
            2,
            6,
            3,
            0,
        )
        assert applied.opciones == ("calificacion", "datos_requeridos", "modalidad_pago")
        situations = _mine(db, tenant.id)
        assert len(situations) == 9
        assert all(s.origen == "sugerida_rubro" and s.plantilla_version == 2 for s in situations)
        assert {s.plantilla_clave for s in situations if s.estado == "borrador"} == WITH_FIELDS
        agent = _agent(db, tenant.id)
        assert agent.plantilla_rubro_version == 2
        options = agent.process_options or {}
        assert options["calificacion"]["activa"] is True
        assert "dni" in options["datos_requeridos"] and options["modalidad_pago"] == "previo"

    def test_the_values_it_gives_make_a_suggestion_active(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        RubroTemplateService(db, tenant.id).apply(
            {"educacion.factura": {"plazo_emision": "  48 horas "}}, by=None
        )

        factura = next(s for s in _mine(db, tenant.id) if s.plantilla_clave == "educacion.factura")
        assert factura.estado == "activa"
        assert factura.que_hace.endswith("(En cuánto tiempo emiten la factura: 48 horas)")

    def test_applying_again_only_brings_what_is_missing(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        service = RubroTemplateService(db, tenant.id)
        service.apply(by=None)
        deleted = next(s for s in _mine(db, tenant.id) if s.plantilla_clave == GROUP)
        db.delete(deleted)
        db.flush()

        again = service.apply(by=None)

        assert (again.activas, again.borradores, again.ya_estaban) == (0, 1, 8)
        assert len(_mine(db, tenant.id)) == 9

    def test_what_the_business_set_stays_its_own(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        agent = _agent(db, tenant.id)
        agent.process_options = {"modalidad_pago": "contra_entrega"}
        db.flush()

        applied = RubroTemplateService(db, tenant.id).apply(by=None)

        assert "modalidad_pago" not in applied.opciones
        assert (_agent(db, tenant.id).process_options or {})["modalidad_pago"] == "contra_entrega"

    def test_a_copy_made_from_the_old_suggestions_is_recognised(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        legacy = SituationService(db, tenant.id).create(
            SituationInput(
                titulo="Si quieren certificar a un grupo",
                cuando_aplica="Quiere certificar a varias personas.",
                que_hace="Pásalo a una persona.",
            ),
            by=None,
            origen="sugerida_rubro",
        )

        RubroTemplateService(db, tenant.id).view()
        assert legacy.plantilla_clave is None  # reading writes nothing

        applied = RubroTemplateService(db, tenant.id).apply(by=None)

        assert applied.ya_estaban == 1 and len(_mine(db, tenant.id)) == 9
        assert legacy.plantilla_clave == GROUP
        assert legacy.que_hace == "Pásalo a una persona."

    def test_the_old_suggestions_flow_now_keeps_the_key(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        catalog = CatalogRepository(db)
        version = catalog.published("rubro", "instituto")
        assert version is not None
        convenios = next(
            s for s in catalog.suggested_situations(version.id) if s.clave == "educacion.convenios"
        )

        situation = SituationService(db, tenant.id).from_suggested(
            convenios.id, "instituto", {}, by=None
        )

        assert (situation.plantilla_clave, situation.plantilla_version) == (
            "educacion.convenios",
            2,
        )
        assert RubroTemplateService(db, tenant.id).apply(by=None).ya_estaban == 1

    def test_a_rubro_without_a_template_is_refused(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        profile = db.execute(
            select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
        ).scalar_one()
        profile.industry = None
        db.flush()

        with pytest.raises(TemplateError) as refused:
            RubroTemplateService(db, tenant.id).apply(by=None)

        assert refused.value.code == "no_template"
        assert _mine(db, tenant.id) == []


class TestNews:
    def test_a_business_that_never_applied_it_has_no_news(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        _publish_v3(db)

        view = RubroTemplateService(db, tenant.id).view()

        assert (view.version, view.aplicada, view.novedades) == (3, None, ())

    def test_a_newer_version_arrives_item_by_item(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        service = RubroTemplateService(db, tenant.id)
        service.apply({GROUP: {"minimo_personas": "5"}}, by=None)
        group = next(s for s in _mine(db, tenant.id) if s.plantilla_clave == GROUP)
        text = group.que_hace
        _publish_v3(db)

        view = service.view()

        assert (view.version, view.aplicada) == (3, 2)
        assert [(n.tipo, n.item.clave) for n in view.novedades] == [
            ("mejorada", GROUP),
            ("nueva", "educacion.becas"),
        ]
        assert view.novedades[0].situation_id == group.id
        # Nothing changed behind the business's back.
        assert (group.que_hace, group.plantilla_version) == (text, 2)

    def test_taking_an_improvement_keeps_its_state_and_its_values(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        service = RubroTemplateService(db, tenant.id)
        service.apply({GROUP: {"minimo_personas": "5"}}, by=None)
        _publish_v3(db)

        taken = service.accept(GROUP, {"minimo_personas": "5"}, by=None)

        assert taken.estado == "activa" and taken.plantilla_version == 3
        assert taken.que_hace == (
            "Pregunta cuántas personas son y ofrece el precio de grupo con su descuento. "
            "(Desde cuántas personas es grupo: 5)"
        )
        assert [n.item.clave for n in service.view().novedades] == ["educacion.becas"]

    def test_an_improvement_without_its_values_goes_back_to_draft(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        service = RubroTemplateService(db, tenant.id)
        service.apply({GROUP: {"minimo_personas": "5"}}, by=None)
        _publish_v3(db)

        assert service.accept(GROUP, by=None).estado == "borrador"

    def test_taking_a_new_item_creates_it(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        service = RubroTemplateService(db, tenant.id)
        service.apply(by=None)
        _publish_v3(db)

        becas = service.accept("educacion.becas", by=None)

        assert (becas.estado, becas.plantilla_version, becas.origen) == (
            "activa",
            3,
            "sugerida_rubro",
        )
        assert len(_mine(db, tenant.id)) == 10

    def test_marking_them_seen_leaves_the_business_as_it_is(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        service = RubroTemplateService(db, tenant.id)
        service.apply(by=None)
        before = {s.plantilla_clave: s.que_hace for s in _mine(db, tenant.id)}
        _publish_v3(db)

        assert service.mark_seen() == 3

        view = service.view()
        assert (view.aplicada, view.novedades) == (3, ())
        assert {s.plantilla_clave: s.que_hace for s in _mine(db, tenant.id)} == before

    def test_an_item_not_in_the_template_is_refused(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        with pytest.raises(TemplateError) as refused:
            RubroTemplateService(db, tenant.id).accept("educacion.no_existe", by=None)

        assert refused.value.code == "unknown_item"


class TestOwnWords:
    def test_a_rubro_rule_in_the_business_words_reaches_the_assistant(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        resolver = LayerResolver(db, tenant.id)

        rule = resolver.rewrite(
            "instituto", "rubro.tono_asesor", "  Habla   como orientador,  cercano. ", by=None
        )

        assert rule.texto_propio == "Habla como orientador, cercano."
        rules = _rubro_rules(db, tenant)
        assert "Habla como orientador, cercano." in rules
        assert not any("asesor académico" in r for r in rules)

    def test_switched_off_it_is_gone_and_it_comes_back_with_its_own_words(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        resolver = LayerResolver(db, tenant.id)
        resolver.rewrite("instituto", "rubro.tono_asesor", "Habla como orientador.", by=None)

        off = resolver.switch("rubro", "instituto", "rubro.tono_asesor", apagada=True, by=None)
        assert off.texto_propio == "Habla como orientador."
        assert "Habla como orientador." not in _rubro_rules(db, tenant)

        on = resolver.switch("rubro", "instituto", "rubro.tono_asesor", apagada=False, by=None)
        assert on.texto_propio == "Habla como orientador."
        assert "Habla como orientador." in _rubro_rules(db, tenant)

    def test_clearing_it_goes_back_to_the_template_text(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        resolver = LayerResolver(db, tenant.id)
        resolver.rewrite("instituto", "rubro.tono_asesor", "Habla como orientador.", by=None)

        back = resolver.rewrite("instituto", "rubro.tono_asesor", "   ", by=None)

        assert back.texto_propio is None
        assert any("asesor académico" in r for r in _rubro_rules(db, tenant))

    def test_a_rule_that_is_not_of_the_rubro_is_refused(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        with pytest.raises(UnknownRule):
            LayerResolver(db, tenant.id).rewrite("instituto", "universal.inicio.1", "x", by=None)

    def test_a_new_version_that_improves_it_says_so_and_keeps_the_business_text(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        resolver = LayerResolver(db, tenant.id)
        assert resolver.news("rubro", "instituto") is None  # starts at the published version
        resolver.rewrite("instituto", "rubro.tono_asesor", "Habla como orientador.", by=None)
        catalog = CatalogRepository(db)
        draft = catalog.new_draft("rubro", "instituto")
        tono = next(r for r in catalog.rules(draft.id) if r.rule_key == "rubro.tono_asesor")
        tono.texto = "Habla como un asesor académico cercano."
        db.flush()
        catalog.publish(draft, by=None)

        news = resolver.news("rubro", "instituto")

        assert news is not None and news.rules == ()
        assert [(r.rule_key, r.texto_propio) for r in news.improved] == [
            ("rubro.tono_asesor", "Habla como orientador.")
        ]
        assert "Habla como orientador." in _rubro_rules(db, tenant)


class TestIsolation:
    def test_applying_and_rewriting_touch_only_that_business(self, db: Session) -> None:
        mine = _tenant(db, template=UNIVERSAL)
        theirs = _tenant(db, template=UNIVERSAL)

        RubroTemplateService(db, mine.id).apply(by=None)
        LayerResolver(db, mine.id).rewrite(
            "instituto", "rubro.tono_asesor", "Habla como orientador.", by=None
        )

        assert _mine(db, theirs.id) == []
        assert _agent(db, theirs.id).plantilla_rubro_version is None
        assert "Habla como orientador." not in _rubro_rules(db, theirs)
        assert RubroTemplateService(db, theirs.id).view().aplicada is None


class TestTheApi:
    async def _instituto(self, api: AsyncClient, mail: Any) -> tuple[uuid.UUID, dict[str, str]]:
        tenant_id, headers = await _account(api, mail)
        saved = await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Capacita", "industry": "instituto"},
        )
        assert saved.status_code == 200, saved.text
        return tenant_id, headers

    async def test_the_owner_sees_applies_and_reads_the_template(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        tenant_id, headers = await self._instituto(api, mail)

        before = (await api.get(f"{A}/rubro/plantilla", headers=headers)).json()
        applied = await api.post(
            f"{A}/rubro/plantilla/aplicar",
            headers=headers,
            json={"valores": {"educacion.malla": {"clave_recurso": "malla"}}},
        )
        after = (await api.get(f"{A}/rubro/plantilla", headers=headers)).json()

        assert (before["rubro"], before["version"], before["aplicada"]) == ("instituto", 2, None)
        assert len(before["situaciones"]) == 9
        assert before["opciones"]["modalidad_pago"] == "previo"
        assert applied.status_code == 200, applied.text
        assert applied.json()["activas"] == 7 and applied.json()["borradores"] == 2
        assert after["aplicada"] == 2 and after["novedades"] == []
        assert [job for job, *_ in queue.jobs].count(EMBED_JOB) == 1
        entries = db.execute(
            select(ConfigHistory.entidad, ConfigHistory.accion).where(
                ConfigHistory.tenant_id == tenant_id
            )
        ).all()
        assert entries.count(("situacion", "crear")) == 9
        assert ("perfil", "editar") in entries

    async def test_undoing_the_options_brings_back_the_process_as_it_was(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        tenant_id, headers = await self._instituto(api, mail)
        agent = db.execute(
            select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)
        ).scalar_one_or_none()
        before = dict(agent.process_options or {}) if agent is not None else {}
        applied = await api.post(f"{A}/rubro/plantilla/aplicar", headers=headers, json={})
        assert applied.status_code == 200, applied.text
        entry = db.execute(
            select(ConfigHistory).where(
                ConfigHistory.tenant_id == tenant_id, ConfigHistory.entidad == "perfil"
            )
        ).scalar_one()

        ConfigHistoryService(db, tenant_id, None).undo(entry)

        assert (_agent(db, tenant_id).process_options or {}) == before

    async def test_a_rule_in_its_own_words_round_trips_and_can_be_undone(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, headers = await self._instituto(api, mail)
        url = f"{A}/rules/rubro/rubro.tono_asesor/texto"

        saved = await api.put(url, headers=headers, json={"texto": "Habla como orientador."})
        unknown = await api.put(
            f"{A}/rules/rubro/rubro.no_existe/texto", headers=headers, json={"texto": "x"}
        )

        assert saved.status_code == 200, saved.text
        assert saved.json()["texto_propio"] == "Habla como orientador."
        assert unknown.status_code == 404 and unknown.json()["error"] == "unknown_rule"
        entry = db.execute(
            select(ConfigHistory).where(
                ConfigHistory.tenant_id == tenant_id, ConfigHistory.entidad == "interruptor"
            )
        ).scalar_one()
        ConfigHistoryService(db, tenant_id, None).undo(entry)
        rule = next(
            r
            for r in LayerResolver(db, tenant_id).rules("rubro", "instituto")
            if r.rule_key == "rubro.tono_asesor"
        )
        assert rule.texto_propio is None

    async def test_a_member_without_the_configuration_role_cannot_apply_it(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        tenant_id, headers = await self._instituto(api, mail)
        _role(db, tenant_id, "operator")

        refused = await api.post(f"{A}/rubro/plantilla/aplicar", headers=headers, json={})

        assert refused.status_code == 403
        assert _mine(db, tenant_id) == []

    async def test_a_business_whose_rubro_has_no_template_is_told_so(
        self, api: AsyncClient, mail: Any, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)
        await api.put(
            f"{TENANT}/business-profile", headers=headers, json={"business_name": "Sin rubro"}
        )

        view = (await api.get(f"{A}/rubro/plantilla", headers=headers)).json()
        refused = await api.post(f"{A}/rubro/plantilla/aplicar", headers=headers, json={})

        if view["version"] is None:
            assert refused.status_code == 422 and refused.json()["error"] == "no_template"
        else:  # the account starts in a rubro that has one
            assert refused.status_code == 200


class TestTheOperatorKeepsTheKeys:
    def test_a_title_gives_a_stable_key(self) -> None:
        assert key_from_title("¿Si piden FACTURA?") == "situacion.si_piden_factura"
        assert key_from_title("¡¡!!") == "situacion.sin_titulo"
