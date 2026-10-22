"""The rubro learns and the writer helps (017, phase 4)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_platform_llm
from app.learning.drafter import DRAFTER_PROMPT, DraftError, draft_card
from app.learning.rubro import candidates
from app.models.audit import AuditLog
from app.models.business import BusinessProfile
from app.models.situation import Situation
from app.providers.llm import MockLLMProvider
from app.providers.llm.base import ChatResult
from app.repositories.catalog import CatalogRepository
from app.repositories.learning import LearningSettingsRepository
from app.services.situations import SituationInput, SituationService
from tests.integration.test_assistant_config_api import _account
from tests.integration.test_learning import _on
from tests.integration.test_platform_admin import _admin, _register
from tests.integration.test_turn_pipeline import _tenant

A = "/api/v1/assistant"
LEARN = "/api/v1/platform-admin/learning"


class Writer(MockLLMProvider):
    def __init__(self, answer: Any) -> None:
        super().__init__()
        self.answer = answer
        self.seen: list[str] = []

    async def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
        assert messages[0].content == DRAFTER_PROMPT
        self.seen.append(messages[1].content)
        return ChatResult(
            content=self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        )


CARD = {
    "titulo": "Piden factura",
    "cuando_aplica": "El cliente pide factura",
    "que_hace": "Pide su RUC y razón social antes de cerrar el pedido.",
    "derivar": False,
    "siempre": False,
    "choques": [],
    "avisos": [],
}


class TestTheAssistedWriter:
    async def test_free_words_become_a_card_and_nothing_is_saved(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        writer = Writer(CARD)

        draft = await draft_card(db, tenant.id, writer, "si piden factura, que me pasen su RUC")

        assert (draft.titulo, draft.derivar, draft.choques) == ("Piden factura", False, [])
        assert "si piden factura, que me pasen su RUC" in writer.seen[0]
        assert db.execute(select(Situation)).first() is None

    async def test_it_warns_of_the_rule_it_contradicts(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        existing = SituationService(db, tenant.id).create(
            SituationInput("Sin facturas", "Piden factura", "Di que no se emiten facturas"), by=None
        )
        existing.estado = "activa"
        db.flush()
        writer = Writer(
            {**CARD, "choques": [{"con": "S1", "motivo": "Una dice que no hay facturas"}]}
        )

        draft = await draft_card(db, tenant.id, writer, "si piden factura, que me pasen su RUC")

        assert "S1: (situación) Piden factura → Di que no se emiten facturas" in writer.seen[0]
        assert draft.choques[0]["motivo"] == "Una dice que no hay facturas"
        assert "Di que no se emiten facturas" in draft.choques[0]["con"]

    async def test_a_label_the_model_made_up_is_ignored(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)

        draft = await draft_card(
            db,
            tenant.id,
            Writer({**CARD, "choques": [{"con": "S99", "motivo": "x"}]}),
            "regla de prueba",
        )

        assert draft.choques == []

    async def test_a_similar_situation_is_found_by_meaning_too(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        llm = Writer(CARD)
        existing = SituationService(db, tenant.id).create(
            SituationInput("Facturas", "El cliente pide factura", "Pide el RUC"), by=None
        )
        existing.estado = "activa"
        existing.embedding = list(
            (await llm.embed([CARD["cuando_aplica"]], input_type="passage"))[0]
        )
        existing.embedding_model = llm.embedding_model
        db.flush()

        draft = await draft_card(db, tenant.id, llm, "si piden factura, que me pasen su RUC")

        assert [c["con"] for c in draft.choques] == ["Facturas"]

    @pytest.mark.parametrize(
        ("setup", "code"),
        [
            ("off", "learning_off"),
            ("short", "too_short"),
            ("spent", "budget"),
            ("junk", "unreadable"),
        ],
    )
    async def test_what_cannot_be_drafted_says_why(
        self, db: Session, setup: str, code: str
    ) -> None:
        tenant = _tenant(db, template="productos")
        if setup != "off":
            _on(db, tenant, ceiling=0 if setup == "spent" else 20)
        writer = Writer("esto no es json" if setup == "junk" else CARD)
        words = "hola" if setup == "short" else "si piden factura, que me pasen su RUC"

        with pytest.raises(DraftError) as caught:
            await draft_card(db, tenant.id, writer, words)

        assert caught.value.code == code

    async def test_the_endpoint_returns_the_card_and_what_is_left_of_the_week(
        self, api: AsyncClient, mail: Any, db: Session, app: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        writer = Writer(CARD)
        app.dependency_overrides[get_platform_llm] = lambda: writer
        refused = await api.post(
            f"{A}/situations/redactar", headers=headers, json={"texto": "si piden factura, RUC"}
        )
        settings = LearningSettingsRepository(db, tenant_id).ensure()
        settings.activo, settings.tope_semanal = True, 3
        db.commit()

        response = await api.post(
            f"{A}/situations/redactar",
            headers=headers,
            json={"texto": "si piden factura, que me pasen su RUC"},
        )

        assert refused.status_code == 409 and refused.json()["error"] == "learning_off"
        assert response.status_code == 200, response.text
        assert response.json()["titulo"] == "Piden factura" and response.json()["quedan"] == 2


def _business(
    db: Session, rubro: str, *, share: bool, label: str, where: str, does: str
) -> uuid.UUID:
    tenant = _tenant(db, template="productos")
    profile = db.execute(
        select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if profile is None:
        profile = BusinessProfile(tenant_id=tenant.id)
        db.add(profile)
    profile.industry = rubro
    settings = LearningSettingsRepository(db, tenant.id).ensure()
    settings.compartir_con_rubro = share
    situation = SituationService(db, tenant.id).create(SituationInput(label, where, does), by=None)
    situation.estado = "activa"
    situation.origen = "aprendida"
    db.flush()
    return tenant.id


async def _embed_all(db: Session, llm: MockLLMProvider) -> None:
    for row in db.execute(select(Situation)).scalars():
        row.embedding = list((await llm.embed([row.cuando_aplica], input_type="passage"))[0])
        row.embedding_model = llm.embedding_model
    db.flush()


class TestWhatTheRubroLearns:
    async def test_the_same_rule_in_two_businesses_that_allowed_it_is_a_candidate(
        self, db: Session
    ) -> None:
        llm = MockLLMProvider()
        _business(
            db, "retail", share=True, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        _business(
            db,
            "retail",
            share=True,
            label="Facturas",
            where="Piden factura",
            does="Pide RUC y razón social",
        )
        _business(
            db,
            "retail",
            share=True,
            label="Horario",
            where="Preguntan el horario",
            does="Dice que abrimos de 9 a 6",
        )
        await _embed_all(db, llm)

        found = candidates(db, "retail", model=llm.embedding_model)

        assert [(c.negocios, c.con_datos) for c in found] == [(2, False)]
        assert found[0].cuando_aplica == "Piden factura" and len(found[0].variantes) == 1

    async def test_a_business_that_did_not_allow_it_is_never_counted(self, db: Session) -> None:
        llm = MockLLMProvider()
        _business(
            db, "retail", share=True, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        _business(
            db, "retail", share=False, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        await _embed_all(db, llm)

        assert candidates(db, "retail", model=llm.embedding_model) == []

    async def test_another_rubro_the_rubros_own_cards_and_old_free_text_are_left_out(
        self, db: Session
    ) -> None:
        llm = MockLLMProvider()
        _business(
            db, "retail", share=True, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        _business(
            db, "salud", share=True, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        other = _business(
            db, "retail", share=True, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        db.execute(
            text("UPDATE situations SET origen = 'sugerida_rubro' WHERE tenant_id = :t"),
            {"t": other},
        )
        await _embed_all(db, llm)

        assert candidates(db, "retail", model=llm.embedding_model) == []

    async def test_a_card_that_still_carries_personal_data_is_flagged(self, db: Session) -> None:
        llm = MockLLMProvider()
        for _ in range(2):
            _business(
                db,
                "retail",
                share=True,
                label="Contacto",
                where="Piden hablar con alguien",
                does="Dile que escriba al 987654321",
            )
        await _embed_all(db, llm)

        (found,) = candidates(db, "retail", model=llm.embedding_model)

        assert found.con_datos is True

    async def test_one_business_alone_is_not_a_pattern(self, db: Session) -> None:
        llm = MockLLMProvider()
        _business(
            db, "retail", share=True, label="Factura", where="Piden factura", does="Pide el RUC"
        )
        await _embed_all(db, llm)

        assert candidates(db, "retail", model=llm.embedding_model) == []


class TestThePlatformPanel:
    @pytest.fixture
    def llm(self, app: Any) -> MockLLMProvider:
        model = MockLLMProvider()
        app.dependency_overrides[get_platform_llm] = lambda: model
        return model

    async def test_only_the_operator_sees_it(
        self, api: AsyncClient, mail: Any, db: Session, llm: MockLLMProvider
    ) -> None:
        headers, _ = await _register(api, mail, db)

        listing = await api.get(f"{LEARN}/candidates?rubro=retail", headers=headers)
        adopting = await api.post(
            f"{LEARN}/adopt",
            headers=headers,
            json={"rubro": "retail", "titulo": "x", "cuando_aplica": "y", "que_hace": "z"},
        )

        assert listing.status_code == 404 and adopting.status_code == 404

    async def test_adopting_writes_to_the_open_draft_once_and_is_audited(
        self, api: AsyncClient, mail: Any, db: Session, llm: MockLLMProvider
    ) -> None:
        headers, _ = await _admin(api, mail, db)
        body = {
            "rubro": "retail",
            "titulo": "Piden factura",
            "cuando_aplica": "El cliente pide factura",
            "que_hace": "Pide su RUC y razón social.",
        }

        first = await api.post(f"{LEARN}/adopt", headers=headers, json=body)
        again = await api.post(f"{LEARN}/adopt", headers=headers, json=body)
        other = await api.post(f"{LEARN}/adopt", headers=headers, json={**body, "titulo": "Otra"})

        assert first.status_code == 200, first.text
        assert first.json() == {"version": 2, "creada": True}
        assert again.status_code == 409 and again.json()["error"] == "already_there"
        assert other.json() == {"version": 2, "creada": False}
        draft = CatalogRepository(db).version("rubro", "retail", 2)
        assert draft is not None
        titles = [s.titulo for s in CatalogRepository(db).suggested_situations(draft.id)]
        assert titles[-2:] == ["Piden factura", "Otra"]  # after the ones the rubro already had
        assert len(titles) > 2
        assert (
            db.execute(
                select(AuditLog).where(AuditLog.action == "catalog.learned_situation_adopted")
            )
            .scalars()
            .all()
        )

    async def test_personal_data_or_an_unknown_rubro_is_refused(
        self, api: AsyncClient, mail: Any, db: Session, llm: MockLLMProvider
    ) -> None:
        headers, _ = await _admin(api, mail, db)
        body = {
            "rubro": "retail",
            "titulo": "Contacto",
            "cuando_aplica": "Piden hablar",
            "que_hace": "Escribe al 987654321",
        }

        leaky = await api.post(f"{LEARN}/adopt", headers=headers, json=body)
        unknown = await api.post(
            f"{LEARN}/adopt",
            headers=headers,
            json={**body, "rubro": "no_existe", "que_hace": "Pide el RUC"},
        )

        assert leaky.status_code == 422 and leaky.json()["error"] == "personal_data"
        assert unknown.status_code == 404
