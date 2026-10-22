"""The agent proposes, a person decides: budget, suggestions, history and the inbox (017, 019 H)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.catalog import consultar_conocimiento
from app.agent.tools.registry import ToolContext
from app.learning.budget import LearningBudget, week_start
from app.learning.history import ConfigHistoryService, HistoryError, situation_snapshot
from app.learning.questions import record_unanswered, suggest_from_questions
from app.learning.suggestions import (
    Proposal,
    SuggestionError,
    SuggestionService,
    find_clashes,
    fingerprint,
)
from app.models.learning import ConfigHistory, Suggestion, UnansweredQuestion
from app.models.sales_config import Faq
from app.models.situation import Situation
from app.models.tenant import Tenant
from app.providers.llm import MockLLMProvider
from app.repositories.conversation import MessageRepository
from app.repositories.learning import LearningSettingsRepository
from app.services.situations import SituationInput, SituationService
from tests.integration.test_assistant_config_api import _account, _role, queue  # noqa: F401
from tests.integration.test_crm_leads import _user
from tests.integration.test_turn_pipeline import _conversation, _tenant

A = "/api/v1/assistant"
TENANT = "/api/v1/tenant"


def _on(db: Session, tenant: Any, ceiling: int = 20) -> None:
    settings = LearningSettingsRepository(db, tenant.id).ensure()
    settings.activo = True
    settings.tope_semanal = ceiling
    db.flush()


def _situation_proposal(**extra: Any) -> Proposal:
    return Proposal(
        tipo="situacion",
        fuente="toma_de_control",
        resumen="Cuando piden factura, pide el RUC",
        propuesta={
            "titulo": "Piden factura",
            "cuando_aplica": "El cliente pide factura o dice que necesita factura",
            "que_hace": "Pide su RUC y razón social antes de cerrar el pedido.",
            **extra,
        },
        key="El cliente pide factura",
    )


class TestTheBudget:
    def test_it_is_off_until_the_owner_turns_it_on(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")

        budget = LearningBudget(db, tenant.id)

        assert budget.enabled is False and budget.ceiling == 20 and budget.used == 0

    def test_it_spends_up_to_the_ceiling_and_then_refuses(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant, ceiling=3)
        budget = LearningBudget(db, tenant.id)

        taken = [budget.spend() for _ in range(5)]

        assert taken == [True, True, True, False, False]
        assert (budget.used, budget.left) == (3, 0)

    def test_a_request_bigger_than_the_ceiling_takes_nothing(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant, ceiling=3)
        budget = LearningBudget(db, tenant.id)

        assert budget.spend(4) is False
        assert budget.used == 0 and budget.spend(3) is True

    def test_the_next_monday_starts_a_new_week(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant, ceiling=1)
        sunday = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
        monday = sunday + timedelta(hours=2)
        assert LearningBudget(db, tenant.id, now=sunday).spend() is True
        assert LearningBudget(db, tenant.id, now=sunday).spend() is False

        assert LearningBudget(db, tenant.id, now=monday).spend() is True
        assert week_start(sunday).weekday() == 0 and week_start(monday) > week_start(sunday)

    def test_audio_minutes_are_added_up(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        budget = LearningBudget(db, tenant.id)

        budget.spend_audio(Decimal("2.5"))
        budget.spend_audio(Decimal("1.25"))

        assert budget.audio_minutes == Decimal("3.75")


class TestSuggestions:
    def test_a_proposal_is_not_made_twice_even_after_it_was_rejected(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        first = service.propose(_situation_proposal())
        assert first is not None
        service.reject(first, "No es así como lo hacemos")

        again = service.propose(_situation_proposal())

        assert again is None
        assert first.estado == "rechazada" and first.nota == "No es así como lo hacemos"

    def test_the_same_words_with_other_accents_are_the_same_proposal(self) -> None:
        assert fingerprint("dato", "¿Hacen envíos a provincia?") == fingerprint(
            "dato", "hacen envios a  PROVINCIA"
        )
        assert fingerprint("dato", "x") != fingerprint("situacion", "x")

    def test_approving_puts_the_situation_in_force_and_leaves_a_line_in_the_history(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        reviewer = _user(db)
        service = SuggestionService(db, tenant.id, reviewer)
        suggestion = service.propose(_situation_proposal())
        assert suggestion is not None

        service.approve(suggestion)

        situation = db.get(Situation, uuid.UUID(suggestion.aplicada["situation_id"]))
        assert situation is not None
        assert (situation.estado, situation.origen) == ("activa", "aprendida")
        assert suggestion.estado == "aprobada" and suggestion.decidida_por == reviewer
        (line,) = db.execute(select(ConfigHistory)).scalars().all()
        assert (line.entidad, line.accion, line.sugerencia_id) == (
            "situacion",
            "crear",
            suggestion.id,
        )

    def test_the_reviewer_may_edit_before_approving(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(_situation_proposal())
        assert suggestion is not None

        service.approve(
            suggestion, edited={"que_hace": "Pide el RUC y el correo.", "derivar": True}
        )

        situation = db.get(Situation, uuid.UUID(suggestion.aplicada["situation_id"]))
        assert situation is not None
        assert (situation.que_hace, situation.derivar) == ("Pide el RUC y el correo.", True)
        assert situation.titulo == "Piden factura"

    def test_an_edit_the_card_cannot_take_is_refused_and_nothing_is_decided(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(_situation_proposal())
        assert suggestion is not None

        with pytest.raises(SuggestionError) as caught:
            service.approve(suggestion, edited={"titulo": "x" * 81})

        assert caught.value.code == "invalid_edit" and suggestion.estado == "pendiente"
        assert db.execute(select(Situation)).first() is None

    def test_it_can_be_tried_as_a_draft_first_and_approving_activates_that_draft(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(_situation_proposal())
        assert suggestion is not None

        draft_id = service.draft(suggestion)
        assert service.draft(suggestion) == draft_id  # asking again finds it
        draft = db.get(Situation, draft_id)
        assert draft is not None and draft.estado == "borrador"
        service.approve(suggestion)

        assert draft.estado == "activa"
        assert len(db.execute(select(Situation)).scalars().all()) == 1

    def test_rejecting_throws_the_draft_away(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(_situation_proposal())
        assert suggestion is not None
        service.draft(suggestion)

        service.reject(suggestion)

        assert db.execute(select(Situation)).first() is None and suggestion.aplicada == {}

    def test_what_is_decided_cannot_be_decided_again(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(_situation_proposal())
        assert suggestion is not None
        service.approve(suggestion)

        with pytest.raises(SuggestionError) as approve_again:
            service.approve(suggestion)
        with pytest.raises(SuggestionError) as reject_it:
            service.reject(suggestion)

        assert approve_again.value.code == reject_it.value.code == "not_pending"

    def test_a_fact_needs_the_answer_the_reviewer_writes(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(
            Proposal(
                tipo="dato",
                fuente="pregunta_sin_respuesta",
                resumen="Te preguntaron si hacen envíos a provincia",
                propuesta={"pregunta": "¿Hacen envíos a provincia?"},
                key="¿Hacen envíos a provincia?",
            )
        )
        assert suggestion is not None

        with pytest.raises(SuggestionError) as caught:
            service.approve(suggestion)
        service.approve(suggestion, answer="Sí, por Olva Courier, de 3 a 5 días.")

        assert caught.value.code == "answer_required"
        faq = db.execute(select(Faq)).scalar_one()
        assert (faq.pregunta, faq.respuesta) == (
            "¿Hacen envíos a provincia?",
            "Sí, por Olva Courier, de 3 a 5 días.",
        )

    async def test_a_new_card_is_warned_about_the_one_it_looks_like(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        llm = MockLLMProvider()
        existing = SituationService(db, tenant.id).create(
            SituationInput("Facturas", "Piden factura", "Pide el RUC"), by=None
        )
        existing.estado = "activa"
        vector = (await llm.embed(["Piden factura"], input_type="passage"))[0]
        existing.embedding = list(vector)
        existing.embedding_model = llm.embedding_model
        db.flush()

        clashes = await find_clashes(db, tenant.id, llm, cuando_aplica="Piden factura")
        unrelated = await find_clashes(db, tenant.id, llm, cuando_aplica="Preguntan por el horario")

        assert [c["id"] for c in clashes] == [str(existing.id)]
        assert unrelated == []


class TestTheHistory:
    def _service(self, db: Session, tenant: Any) -> ConfigHistoryService:
        return ConfigHistoryService(db, tenant.id, _user(db))

    def test_undoing_a_creation_deletes_it(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        situation = SituationService(db, tenant.id).create(
            SituationInput("Facturas", "Piden factura", "Pide el RUC"), by=None
        )
        history = self._service(db, tenant)
        entry = history.record(
            "situacion", "crear", "Creó Facturas", entidad_id=situation.id,
            despues=situation_snapshot(situation),
        )  # fmt: skip

        undone = history.undo(entry)

        assert db.get(Situation, situation.id) is None
        assert undone.accion == "deshacer" and entry.deshecho_por == undone.id

    def test_undoing_an_edit_puts_the_before_back(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SituationService(db, tenant.id)
        situation = service.create(
            SituationInput("Facturas", "Piden factura", "Pide el RUC"), by=None
        )
        before = situation_snapshot(situation)
        service.update(situation, SituationInput("Facturas 2", "Piden boleta", "Pide el DNI"))
        history = self._service(db, tenant)
        entry = history.record(
            "situacion", "editar", "Editó", entidad_id=situation.id, antes=before,
            despues=situation_snapshot(situation),
        )  # fmt: skip

        history.undo(entry)

        assert (situation.titulo, situation.cuando_aplica, situation.que_hace) == (
            "Facturas", "Piden factura", "Pide el RUC",
        )  # fmt: skip
        assert situation.embedding is None  # «cuándo aplica» changed back: to be embedded again

    def test_undoing_a_deletion_brings_it_back_with_the_same_id(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SituationService(db, tenant.id)
        situation = service.create(
            SituationInput("Facturas", "Piden factura", "Pide el RUC"), by=None
        )
        situation.estado = "activa"
        before, situation_id = situation_snapshot(situation), situation.id
        service.delete(situation)
        history = self._service(db, tenant)
        entry = history.record(
            "situacion", "borrar", "Borró", entidad_id=situation_id, antes=before
        )

        history.undo(entry)

        back = db.get(Situation, situation_id)
        assert back is not None and (back.titulo, back.estado) == ("Facturas", "activa")

    def test_a_change_is_undone_once_and_an_undo_is_not_undone(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        situation = SituationService(db, tenant.id).create(
            SituationInput("Facturas", "Piden factura", "Pide el RUC"), by=None
        )
        history = self._service(db, tenant)
        entry = history.record("situacion", "crear", "Creó", entidad_id=situation.id)
        undone = history.undo(entry)

        with pytest.raises(HistoryError) as twice:
            history.undo(entry)
        with pytest.raises(HistoryError) as the_undo:
            history.undo(undone)

        assert (twice.value.code, the_undo.value.code) == ("already_undone", "not_undoable")

    def test_undoing_the_fact_a_suggestion_added_removes_it(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        service = SuggestionService(db, tenant.id, _user(db))
        suggestion = service.propose(
            Proposal(
                "dato",
                "pregunta_sin_respuesta",
                "¿Envíos?",
                {"pregunta": "¿Hacen envíos?"},
                "¿Hacen envíos?",
            )
        )
        assert suggestion is not None
        service.approve(suggestion, answer="Sí, a todo el Perú.")
        entry = db.execute(select(ConfigHistory)).scalar_one()

        ConfigHistoryService(db, tenant.id, _user(db)).undo(entry)

        assert db.execute(select(Faq)).first() is None


class TestUnansweredQuestions:
    def test_nothing_is_kept_while_learning_is_off(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")

        kept = record_unanswered(db, tenant.id, "¿Hacen envíos a provincia?", None)

        assert kept is False and db.execute(select(UnansweredQuestion)).first() is None

    def test_a_question_is_kept_without_the_personal_data_in_it(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)

        record_unanswered(
            db, tenant.id, "Soy Ana Pérez, mi cel 987654321: ¿hacen envíos a Piura?", None
        )

        row = db.execute(select(UnansweredQuestion)).scalar_one()
        assert "Ana" not in row.pregunta and "987654321" not in row.pregunta
        assert "envíos a Piura" in row.pregunta

    def test_a_question_that_is_only_personal_data_is_not_kept(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)

        record_unanswered(db, tenant.id, "987654321 45678912 ana@mail.com", None)

        assert db.execute(select(UnansweredQuestion)).first() is None

    def test_asked_twice_it_becomes_one_suggestion_and_never_a_second(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        for _ in range(2):
            record_unanswered(db, tenant.id, "¿Hacen envíos a provincia?", None)
        record_unanswered(db, tenant.id, "¿Aceptan tarjeta?", None)  # once: forgotten

        first = suggest_from_questions(db, tenant.id)
        second = suggest_from_questions(db, tenant.id)

        assert (first, second) == (1, 0)
        (suggestion,) = db.execute(select(Suggestion)).scalars().all()
        assert suggestion.tipo == "dato" and suggestion.propuesta == {
            "pregunta": "¿Hacen envíos a provincia?"
        }
        assert "2 veces" in suggestion.resumen

    def test_a_rejected_question_is_not_proposed_again_when_it_is_asked_again(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        for _ in range(2):
            record_unanswered(db, tenant.id, "¿Hacen envíos a provincia?", None)
        suggest_from_questions(db, tenant.id)
        suggestion = db.execute(select(Suggestion)).scalar_one()
        SuggestionService(db, tenant.id, _user(db)).reject(suggestion)
        for _ in range(3):
            record_unanswered(db, tenant.id, "¿Hacen envíos a provincia?", None)
        db.execute(
            UnansweredQuestion.__table__.update().values(estado="abierta")  # a sweep finds it again
        )

        assert suggest_from_questions(db, tenant.id) == 0

    async def test_the_knowledge_tool_notes_a_question_it_could_not_answer(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        conversation = _conversation(db, tenant, channel="whatsapp")
        context = ToolContext(
            session=db, tenant_id=tenant.id, llm=MockLLMProvider(), conversation_id=conversation.id
        )

        result = await consultar_conocimiento(context, {"consulta": "¿hacen envíos a provincia?"})

        assert result.status == "no_disponible"
        row = db.execute(select(UnansweredQuestion)).scalar_one()
        assert row.conversaciones == [conversation.id]

    async def test_the_simulator_leaves_no_trace(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        conversation = _conversation(db, tenant)
        context = ToolContext(
            session=db,
            tenant_id=tenant.id,
            llm=MockLLMProvider(),
            conversation_id=conversation.id,
            sandbox=True,
        )

        await consultar_conocimiento(context, {"consulta": "¿hacen envíos a provincia?"})

        assert db.execute(select(UnansweredQuestion)).first() is None


class TestTheInbox:
    async def _pending(self, db: Session, tenant_id: uuid.UUID, **extra: Any) -> Suggestion:
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        conversation = _conversation(db, tenant, channel="whatsapp")
        repo = MessageRepository(db, tenant_id)
        repo.append(
            conversation_id=conversation.id,
            direction="inbound",
            author="customer",
            content="Necesito factura",
        )
        repo.append(
            conversation_id=conversation.id,
            direction="outbound",
            author="human_operator",
            content="Claro, ¿me das tu RUC?",
        )
        proposal = _situation_proposal(**extra)
        suggestion = SuggestionService(db, tenant_id).propose(
            Proposal(
                proposal.tipo,
                proposal.fuente,
                proposal.resumen,
                proposal.propuesta,
                proposal.key,
                conversaciones=[conversation.id],
            )  # fmt: skip
        )
        assert suggestion is not None
        db.commit()
        return suggestion

    async def test_it_needs_a_login_and_a_reviewer(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        _role(db, tenant_id, "operator", reviewer=False)

        anonymous = await api.get(f"{A}/suggestions")
        operator = await api.get(f"{A}/suggestions", headers=headers)
        _role(db, tenant_id, "operator", reviewer=True)
        reviewer = await api.get(f"{A}/suggestions", headers=headers)

        assert anonymous.status_code == 401 and operator.status_code == 403
        assert reviewer.status_code == 200

    async def test_learning_is_off_by_default_and_only_managers_turn_it_on(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, headers = await _account(api, mail)

        initial = await api.get(f"{A}/learning", headers=headers)
        on = await api.put(
            f"{A}/learning", headers=headers, json={"activo": True, "tope_semanal": 5}
        )
        too_much = await api.put(f"{A}/learning", headers=headers, json={"tope_semanal": 501})
        shared = await api.put(f"{A}/learning", headers=headers, json={"compartir_con_rubro": True})
        _role(db, tenant_id, "operator", reviewer=True)
        denied = await api.put(f"{A}/learning", headers=headers, json={"activo": False})

        assert initial.json()["activo"] is False and initial.json()["tope_semanal"] == 20
        assert (
            on.json()["activo"] is True
            and on.json()["tope_semanal"] == 5
            and on.json()["quedan"] == 5
        )
        assert too_much.status_code == 400
        assert shared.json()["compartir_con_rubro"] is True
        assert denied.status_code == 403

    async def test_the_reviewer_reads_the_suggestion_and_the_conversation_behind_it(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        suggestion = await self._pending(db, tenant_id)

        listing = await api.get(f"{A}/suggestions", headers=headers)
        detail = await api.get(f"{A}/suggestions/{suggestion.id}", headers=headers)

        assert [row["id"] for row in listing.json()["items"]] == [str(suggestion.id)]
        body = detail.json()
        assert body["propuesta"]["titulo"] == "Piden factura" and body["estado"] == "pendiente"
        (origin,) = body["origen"]
        assert [line["de"] for line in origin["mensajes"]] == ["cliente", "equipo"]
        assert origin["mensajes"][0]["texto"] == "Necesito factura"

    async def test_approving_edited_puts_it_in_force_and_asks_for_the_embedding(
        self, api: AsyncClient, mail: Any, db: Session, queue: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        suggestion = await self._pending(db, tenant_id)

        response = await api.post(
            f"{A}/suggestions/{suggestion.id}/approve",
            headers=headers,
            json={"editado": {"que_hace": "Pide el RUC y confirma la razón social."}},
        )
        again = await api.post(f"{A}/suggestions/{suggestion.id}/approve", headers=headers, json={})
        situations = await api.get(f"{A}/situations", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["sugerencia"]["estado"] == "aprobada"
        (card,) = situations.json()["items"]
        assert card["estado"] == "activa" and card["origen"] == "aprendida"
        assert card["que_hace"] == "Pide el RUC y confirma la razón social."
        assert again.status_code == 409 and again.json()["error"] == "not_pending"
        assert any(job[0] == "embed_situations" for job in queue.jobs)

    async def test_a_fact_needs_an_answer_and_is_indexed_as_knowledge(
        self, api: AsyncClient, mail: Any, db: Session, queue: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        suggestion = SuggestionService(db, tenant_id).propose(
            Proposal(
                "dato",
                "pregunta_sin_respuesta",
                "¿Envíos?",
                {"pregunta": "¿Hacen envíos a provincia?"},
                "envios",
            )
        )
        assert suggestion is not None
        db.commit()

        without = await api.post(
            f"{A}/suggestions/{suggestion.id}/approve", headers=headers, json={}
        )
        with_answer = await api.post(
            f"{A}/suggestions/{suggestion.id}/approve",
            headers=headers,
            json={"respuesta": "Sí, por Olva Courier."},
        )
        faqs = await api.get(f"{A}/faqs", headers=headers)

        assert without.status_code == 422 and without.json()["error"] == "answer_required"
        assert with_answer.status_code == 200 and with_answer.json()["indexada"] is True
        assert [f["respuesta"] for f in faqs.json()["items"]] == ["Sí, por Olva Courier."]
        assert any(job[0] == "index_knowledge_source" for job in queue.jobs)

    async def test_trying_it_in_the_simulator_makes_a_draft_and_rejecting_removes_it(
        self, api: AsyncClient, mail: Any, db: Session, queue: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        suggestion = await self._pending(db, tenant_id)

        draft = await api.post(f"{A}/suggestions/{suggestion.id}/draft", headers=headers)
        detail = await api.get(f"{A}/suggestions/{suggestion.id}", headers=headers)
        listed = await api.get(f"{A}/situations", headers=headers)
        rejected = await api.post(
            f"{A}/suggestions/{suggestion.id}/reject", headers=headers, json={"nota": "No aplica"}
        )
        after = await api.get(f"{A}/situations", headers=headers)

        assert draft.status_code == 200, draft.text
        assert detail.json()["situation_id"] == draft.json()["situation_id"]
        assert [(s["id"], s["estado"]) for s in listed.json()["items"]] == [
            (draft.json()["situation_id"], "borrador")
        ]
        assert rejected.json()["estado"] == "rechazada" and after.json()["items"] == []

    async def test_another_businesss_suggestion_is_a_404(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, mine = await _account(api, mail)
        theirs_id, _ = await _account(api, mail)
        suggestion = await self._pending(db, theirs_id)

        responses = [
            await api.get(f"{A}/suggestions/{suggestion.id}", headers=mine),
            await api.post(f"{A}/suggestions/{suggestion.id}/approve", headers=mine, json={}),
            await api.post(f"{A}/suggestions/{suggestion.id}/reject", headers=mine, json={}),
            await api.post(f"{A}/suggestions/{suggestion.id}/draft", headers=mine),
        ]
        listing = await api.get(f"{A}/suggestions", headers=mine)

        assert {r.status_code for r in responses} == {404}
        assert listing.json()["items"] == []

    async def test_every_manual_change_is_in_the_history_and_can_be_undone(
        self, api: AsyncClient, mail: Any, db: Session, queue: Any
    ) -> None:
        _, headers = await _account(api, mail)
        body = {"titulo": "Facturas", "cuando_aplica": "Piden factura", "que_hace": "Pide el RUC"}
        created = (await api.post(f"{A}/situations", headers=headers, json=body)).json()
        await api.put(
            f"{A}/situations/{created['id']}",
            headers=headers,
            json={**body, "que_hace": "Pide el RUC y el correo"},
        )
        await api.post(f"{A}/situations/{created['id']}/activar", headers=headers)
        await api.post(f"{A}/situations/{created['id']}/desactivar", headers=headers)
        await api.delete(f"{A}/situations/{created['id']}", headers=headers)

        history = (await api.get(f"{A}/history", headers=headers)).json()["items"]
        kinds = [(row["entidad"], row["accion"]) for row in reversed(history)]
        deletion = next(row for row in history if row["accion"] == "borrar")
        undone = await api.post(f"{A}/history/{deletion['id']}/undo", headers=headers)
        again = await api.post(f"{A}/history/{deletion['id']}/undo", headers=headers)
        back = await api.get(f"{A}/situations/{created['id']}", headers=headers)

        assert kinds == [
            ("situacion", "crear"), ("situacion", "editar"), ("situacion", "estado"),
            ("situacion", "estado"), ("situacion", "borrar"),
        ]  # fmt: skip
        assert undone.status_code == 200, undone.text
        assert again.status_code == 409 and again.json()["error"] == "already_undone"
        assert back.status_code == 200 and back.json()["titulo"] == "Facturas"

    async def test_a_profile_change_is_in_the_history_and_undoing_it_restores_it(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _account(api, mail)
        await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"agent_name": "Leo", "tone": "cercano"}
        )
        await api.put(f"{TENANT}/agent-config", headers=headers, json={"tone": "profesional"})

        history = (await api.get(f"{A}/history", headers=headers)).json()["items"]
        entry = next(row for row in history if row["entidad"] == "perfil")
        undone = await api.post(f"{A}/history/{entry['id']}/undo", headers=headers)
        config = await api.get(f"{TENANT}/agent-config", headers=headers)

        assert "tono" in entry["resumen"] and undone.status_code == 200, undone.text
        assert config.json()["tone"] == "cercano"


class TestTheDailySweep:
    async def test_only_the_businesses_that_turned_it_on_are_worked_on(self, db: Session) -> None:
        from app.workers.learning import learning_sweep

        on, off = _tenant(db, template="productos"), _tenant(db, template="productos")
        _on(db, on)
        for _ in range(2):
            record_unanswered(db, on.id, "¿Hacen envíos a provincia?", None)
        # The one that did not turn it on has a question that could be proposed, and is left alone.
        db.add(
            UnansweredQuestion(
                tenant_id=off.id,
                pregunta="¿Aceptan tarjeta de crédito?",
                huella=fingerprint("pregunta", "¿Aceptan tarjeta de crédito?"),
                veces=5,
            )
        )
        db.flush()

        class Factory:
            def __call__(self) -> Any:
                return _Borrowed(db)

        result = await learning_sweep({"session_factory": Factory()})

        assert result == {"preguntas": 1}
        assert [s.tenant_id for s in db.execute(select(Suggestion)).scalars()] == [on.id]


class _Borrowed:
    """The test's own session, handed out like a worker's and not closed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *exc: object) -> None:
        return None
