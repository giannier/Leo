"""Learning from an exported WhatsApp chat: the pipeline, the worker, the API (017, phase 2)."""

from __future__ import annotations

# ruff: noqa: F811
import io
import json
import re
import zipfile
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.examples import select_examples
from app.agent.prompts.stage import build_stage_prompt
from app.api.deps import get_job_queue, get_redis
from app.learning.importer import (
    REDACTION_PROMPT,
    STYLE_PROMPT,
    ImportFailed,
    run_import,
)
from app.models.audit import AuditLog
from app.models.learning import (
    ChatImport,
    ConfigHistory,
    ExampleConversation,
    Suggestion,
)
from app.process.facts import Facts
from app.process.recompute import recompute_stage  # noqa: F401 - import side effects of the process
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.templates.base import Stage
from app.providers.llm import MockLLMProvider
from app.providers.llm.base import ChatResult
from app.providers.transcription.mock import MockTranscriptionProvider
from app.repositories.business import AgentConfigRepository
from app.repositories.learning import ChatImportRepository, LearningSettingsRepository
from app.workers.chat_import import original_key, process_chat_import, sellers_key
from tests.integration.test_assistant_config_api import _account, queue  # noqa: F401
from tests.integration.test_crm_leads import _user
from tests.integration.test_learning import _Borrowed, _on
from tests.integration.test_turn_pipeline import _tenant
from tests.unit.test_locks import FakeStore

A = "/api/v1/assistant"

CHAT = """\
12/10/2025 14:30 - Los mensajes y llamadas están cifrados de extremo a extremo.
12/10/2025 14:32 - Carla Ríos: Hola, buenas tardes, soy Carla y mi cel es 987654321
12/10/2025 14:33 - Tienda Sol: Hola Carla, ¿en qué te ayudo? 😊
12/10/2025 14:34 - Carla Ríos: Quiero un polo básico pero me parece caro, lo voy a pensar
12/10/2025 14:35 - Tienda Sol: Te entiendo. El polo es de algodón pima y dura años, rinde más
12/10/2025 14:36 - Carla Ríos: Ok, y si compro dos me haces descuento?
12/10/2025 14:37 - Tienda Sol: Con dos tenemos la promo 2x1, te sale mejor. ¿Te lo separo?
12/10/2025 14:38 - Carla Ríos: Sí, te pago por Yape
12/10/2025 14:39 - Tienda Sol: Perfecto, mándame el comprobante y te lo envío hoy 🙌
12/10/2025 14:40 - Tienda Sol: PTT-20251012-WA0001.opus (archivo adjunto)
13/10/2025 10:00 - Luis Soto: Hola, necesito cotizar una gorra, vivo en Av. Larco 123
13/10/2025 10:01 - Tienda Sol: Claro Luis, la gorra cuesta S/ 25, ¿cuántas necesitas?
13/10/2025 10:02 - Luis Soto: Son 10, está muy caro el envío
13/10/2025 10:03 - Tienda Sol: Para 10 el envío es gratis, ya lo incluí 😊
13/10/2025 10:04 - Luis Soto: Genial, te pago por transferencia
13/10/2025 10:05 - Tienda Sol: Perfecto, te paso la cuenta y quedamos listos 🙌
"""


class ScriptedModel(MockLLMProvider):
    """Answers each kind of request the importer makes, by what its system prompt says."""

    def __init__(self, *, broken_redaction: bool = False) -> None:
        super().__init__()
        self.broken = broken_redaction
        self.redaction_inputs: list[str] = []
        self.objection_inputs: list[str] = []

    async def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
        system, user = messages[0].content, messages[1].content
        self.chat_calls.append(type("C", (), {"messages": tuple(messages)})())  # type: ignore[arg-type]
        if system == REDACTION_PROMPT:
            self.redaction_inputs.append(user)
            if self.broken:
                return ChatResult(content="esto no tiene el formato pedido")
            names = {"Carla": "[nombre]", "Luis": "[nombre]"}
            out = user
            for name, placeholder in names.items():
                out = out.replace(name, placeholder)
            return ChatResult(content=out)
        if system == STYLE_PROMPT:
            return ChatResult(
                content=json.dumps({"estilo": "Tutean, mensajes cortos, con emojis."})
            )
        if "objeción" in system:
            self.objection_inputs.append(user)
            return ChatResult(
                content=json.dumps(
                    [
                        {
                            "titulo": "Dicen que es caro",
                            "cuando_aplica": "El cliente dice que el producto o el envío es caro",
                            "que_hace": "Explica el valor (material, duración) y ofrece la promo.",
                        }
                    ]
                )
            )
        if "título" in system:
            ids = sorted({int(i) for i in re.findall(r"^(\d+):", user, re.M)})
            return ChatResult(
                content=json.dumps(
                    [
                        {"i": i, "titulo": f"Caso {i}", "tema": "Cliente duda por el precio"}
                        for i in ids
                    ]
                )
            )
        return ChatResult(content="[]")


def _record(db: Session, tenant: Any) -> ChatImport:
    record = ChatImportRepository(db, tenant.id).add(autorizado_por=_user(db))
    db.commit()  # the API committed it before the worker ran: a rollback must not take it away
    return record


class TestTheAnalysis:
    async def test_a_chat_becomes_suggestions_without_the_personal_data_in_it(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record, model = _record(db, tenant), ScriptedModel()

        outcome = await run_import(
            db, record, CHAT.encode(), ["Tienda Sol"],
            llm=model, transcriber=MockTranscriptionProvider(), actor=record.autorizado_por,
        )  # fmt: skip

        kinds = sorted(s.tipo for s in db.execute(select(Suggestion)).scalars())
        assert (
            kinds.count("estilo") == 1
            and kinds.count("situacion") == 1
            and kinds.count("ejemplo") == 2
        )
        assert outcome.sugerencias == 4 and outcome.conversaciones == 2 and outcome.mensajes == 15
        everything = json.dumps(
            [s.propuesta for s in db.execute(select(Suggestion)).scalars()], ensure_ascii=False
        )
        for private in ("Carla", "Luis", "987654321", "Larco", "Tienda Sol", "Ríos", "Soto"):
            assert private not in everything, private
        assert (
            "[nombre]" in everything and "[teléfono]" in everything and "[dirección]" in everything
        )

    async def test_the_model_never_sees_a_name_the_rules_know_how_to_take_out(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record, model = _record(db, tenant), ScriptedModel()

        await run_import(
            db, record, CHAT.encode(), ["Tienda Sol"],
            llm=model, transcriber=MockTranscriptionProvider(), actor=None,
        )  # fmt: skip

        seen = "\n".join(model.redaction_inputs + model.objection_inputs)
        assert "987654321" not in seen and "Av. Larco" not in seen and "Tienda Sol" not in seen

    async def test_it_spends_from_the_ceiling_of_the_week(self, db: Session) -> None:
        from app.learning.budget import LearningBudget

        tenant = _tenant(db, template="productos")
        _on(db, tenant, ceiling=20)
        record = _record(db, tenant)

        await run_import(
            db, record, CHAT.encode(), ["Tienda Sol"],
            llm=ScriptedModel(), transcriber=MockTranscriptionProvider(), actor=None,
        )  # fmt: skip

        assert 4 <= LearningBudget(db, tenant.id).used <= 6

    async def test_the_same_chat_twice_proposes_nothing_new(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        first = await run_import(
            db, _record(db, tenant), CHAT.encode(), ["Tienda Sol"],
            llm=ScriptedModel(), transcriber=MockTranscriptionProvider(), actor=None,
        )  # fmt: skip
        second = await run_import(
            db, _record(db, tenant), CHAT.encode(), ["Tienda Sol"],
            llm=ScriptedModel(), transcriber=MockTranscriptionProvider(), actor=None,
        )  # fmt: skip

        assert first.sugerencias == 4 and second.sugerencias == 0

    @pytest.mark.parametrize(
        ("setup", "code"),
        [
            ("off", "learning_off"),
            ("poor", "budget"),
            ("no_chat", "no_messages"),
            ("alone", "nothing_to_learn"),
        ],
    )
    async def test_what_cannot_be_learned_from_is_refused_before_spending(
        self, db: Session, setup: str, code: str
    ) -> None:
        tenant = _tenant(db, template="productos")
        if setup != "off":
            _on(db, tenant, ceiling=3 if setup == "poor" else 20)
        record, model = _record(db, tenant), ScriptedModel()
        raw = b"esto no es un chat" if setup == "no_chat" else CHAT.encode()
        sellers = ["Alguien que no escribió"] if setup == "alone" else ["Tienda Sol"]

        with pytest.raises(ImportFailed) as caught:
            await run_import(
                db,
                record,
                raw,
                sellers,
                llm=model,
                transcriber=MockTranscriptionProvider(),
                actor=None,
            )

        assert caught.value.code == code and model.chat_calls == []

    async def test_a_conversation_that_does_not_come_back_clean_is_not_kept(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record = _record(db, tenant)

        with pytest.raises(ImportFailed) as caught:
            await run_import(
                db, record, CHAT.encode(), ["Tienda Sol"],
                llm=ScriptedModel(broken_redaction=True),
                transcriber=MockTranscriptionProvider(), actor=None,
            )  # fmt: skip

        assert caught.value.code == "not_clean"
        assert db.execute(select(Suggestion)).first() is None

    async def test_voice_notes_are_transcribed_and_counted_within_the_ceiling(
        self, db: Session
    ) -> None:
        from app.learning.budget import LearningBudget

        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Chat de WhatsApp con Carla.txt", CHAT)
            archive.writestr("PTT-20251012-WA0001.opus", b"OggS" + b"x" * 60_000)
        transcriber = MockTranscriptionProvider(
            {"PTT-20251012-WA0001.opus": "Mira Carla, te lo dejo en 25 soles esta vez"},
            seconds=30,
        )
        model = ScriptedModel()

        outcome = await run_import(
            db, _record(db, tenant), buffer.getvalue(), ["Tienda Sol"],
            llm=model, transcriber=transcriber, actor=None,
        )  # fmt: skip

        assert transcriber.calls == ["PTT-20251012-WA0001.opus"]
        assert (outcome.audios, outcome.audios_transcritos) == (1, 1)
        assert outcome.minutos_audio == Decimal("0.5")
        assert LearningBudget(db, tenant.id).audio_minutes == Decimal("0.5")
        # What the seller said aloud reaches the objections, with the name taken out.
        assert "te lo dejo en 25 soles" in "\n".join(model.objection_inputs)
        assert "Mira Carla" not in "\n".join(model.objection_inputs)

    async def test_an_audio_that_fails_or_is_too_long_is_left_out_not_fatal(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("chat.txt", CHAT)
            archive.writestr("PTT-20251012-WA0001.opus", b"OggS" + b"x" * 5_000_000)  # ~40 minutes
        transcriber = MockTranscriptionProvider()

        outcome = await run_import(
            db, _record(db, tenant), buffer.getvalue(), ["Tienda Sol"],
            llm=ScriptedModel(), transcriber=transcriber, actor=None,
        )  # fmt: skip

        assert transcriber.calls == [] and outcome.audios == 1 and outcome.audios_transcritos == 0
        assert outcome.sugerencias == 4


class TestTheWorker:
    def _ctx(self, db: Session, store: FakeStore, model: MockLLMProvider) -> dict[str, Any]:
        return {
            "redis": store,
            "session_factory": lambda: _Borrowed(db),
            "llm": model,
            "transcriber": MockTranscriptionProvider(),
        }

    async def test_it_processes_and_deletes_the_original_afterwards(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record = _record(db, tenant)
        store = FakeStore()
        await store.set(original_key(record.id), CHAT.encode())
        await store.set(sellers_key(record.id), json.dumps(["Tienda Sol"]))

        state = await process_chat_import(
            self._ctx(db, store, ScriptedModel()), str(record.id), str(tenant.id)
        )

        assert state == "listo" and record.estado == "listo" and record.sugerencias == 4
        assert store.values == {}  # the original is gone
        assert record.terminada_en is not None and record.error is None

    async def test_it_deletes_the_original_also_when_it_fails_and_says_why_without_the_chat(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record = _record(db, tenant)
        store = FakeStore()
        await store.set(original_key(record.id), CHAT.encode())
        await store.set(sellers_key(record.id), json.dumps(["Tienda Sol"]))

        state = await process_chat_import(
            self._ctx(db, store, ScriptedModel(broken_redaction=True)),
            str(record.id),
            str(tenant.id),
        )

        assert state == "fallido" and record.estado == "fallido"
        assert store.values == {}
        assert record.error and "Carla" not in record.error and "Tienda" not in record.error

    async def test_an_unexpected_error_leaves_nothing_of_the_chat_in_the_record_or_the_store(
        self, db: Session
    ) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record = _record(db, tenant)
        store = FakeStore()
        await store.set(original_key(record.id), CHAT.encode())
        await store.set(sellers_key(record.id), json.dumps(["Tienda Sol"]))

        class Exploding(ScriptedModel):
            async def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
                raise RuntimeError("boom Carla 987654321")

        state = await process_chat_import(
            self._ctx(db, store, Exploding()), str(record.id), str(tenant.id)
        )

        assert state == "fallido" and store.values == {}
        assert "Carla" not in (record.error or "") and "987654321" not in (record.error or "")

    async def test_an_original_that_expired_is_a_clear_failure(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        record = _record(db, tenant)

        state = await process_chat_import(
            self._ctx(db, FakeStore(), ScriptedModel()), str(record.id), str(tenant.id)
        )

        assert state == "fallido" and "súbelo de nuevo" in (record.error or "")


class TestWhatApprovingDoes:
    def _suggestions(self, db: Session, tenant: Any) -> list[Suggestion]:
        return list(
            db.execute(select(Suggestion).where(Suggestion.tenant_id == tenant.id)).scalars()
        )

    async def _imported(self, db: Session) -> tuple[Any, list[Suggestion]]:
        tenant = _tenant(db, template="productos")
        _on(db, tenant)
        await run_import(
            db, _record(db, tenant), CHAT.encode(), ["Tienda Sol"],
            llm=ScriptedModel(), transcriber=MockTranscriptionProvider(), actor=None,
        )  # fmt: skip
        return tenant, self._suggestions(db, tenant)

    async def test_the_style_goes_to_the_prompt_and_undoing_it_takes_it_out(
        self, db: Session
    ) -> None:
        from app.learning.history import ConfigHistoryService
        from app.learning.suggestions import SuggestionService

        tenant, suggestions = await self._imported(db)
        style = next(s for s in suggestions if s.tipo == "estilo")
        AgentConfigRepository(db, tenant.id).upsert(agent_name="Leo")
        service = SuggestionService(db, tenant.id, _user(db))

        service.approve(style)
        config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
        prompt = build_stage_prompt(
            config,
            Facts(),
            Stage.INICIO,
            now=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        entry = db.execute(
            select(ConfigHistory).where(ConfigHistory.entidad == "estilo")
        ).scalar_one()
        ConfigHistoryService(db, tenant.id, _user(db)).undo(entry)
        again = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)

        assert "Así escriben los vendedores del negocio" in prompt.text
        assert "Tutean, mensajes cortos, con emojis" in prompt.text
        assert again.behavior.estilo_vendedor is None

    async def test_a_real_example_is_consulted_when_a_similar_case_comes_up(
        self, db: Session
    ) -> None:
        from app.learning.suggestions import SuggestionService
        from app.services.situations import embed_pending

        tenant, suggestions = await self._imported(db)
        examples = [s for s in suggestions if s.tipo == "ejemplo"]
        SuggestionService(db, tenant.id, _user(db)).approve_many(examples)
        llm = MockLLMProvider()
        assert await embed_pending(db, tenant.id, llm) == 2

        near = await select_examples(db, tenant.id, message="Cliente duda por el precio", llm=llm)
        far = await select_examples(
            db, tenant.id, message="¿Dónde queda su local en Arequipa?", llm=llm
        )

        assert len(near) == 1 and near[0].mensajes[0][0] in ("cliente", "vendedor")
        assert far == []

    async def test_a_business_with_no_examples_pays_for_no_embedding(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        llm = MockLLMProvider()

        found = await select_examples(db, tenant.id, message="Hola", llm=llm)

        assert found == [] and llm.embed_calls == []

    async def test_the_prompt_shows_the_example_and_tells_not_to_copy_its_data(
        self, db: Session
    ) -> None:
        from datetime import UTC, datetime

        from app.agent.examples import SelectedExample

        tenant = _tenant(db, template="productos")
        config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)
        example = SelectedExample(
            id=__import__("uuid").uuid4(),
            titulo="Caso",
            mensajes=(("cliente", "Está caro"), ("vendedor", "Te entiendo, dura años")),
            distancia=0.2,
        )

        prompt = build_stage_prompt(
            config, Facts(), Stage.INICIO, now=datetime.now(UTC), examples=[example]
        )
        plain = build_stage_prompt(config, Facts(), Stage.INICIO, now=datetime.now(UTC))

        assert "EJEMPLO REAL" in prompt.text and "NO copies datos" in prompt.text
        assert (
            "Cliente: Está caro" in prompt.text
            and "Vendedor: Te entiendo, dura años" in prompt.text
        )
        assert "EJEMPLO REAL" not in plain.text and prompt.sections["ejemplos"] <= 350


class TestThePanel:
    @pytest.fixture
    def plumbing(self, app: FastAPI) -> tuple[FakeStore, Any]:
        from tests.integration.test_knowledge_ingestion import RecordingQueue

        store, recording = FakeStore(), RecordingQueue()
        app.dependency_overrides[get_redis] = lambda: store
        app.dependency_overrides[get_job_queue] = lambda: recording
        return store, recording

    async def test_inspect_reads_the_chat_and_stores_nothing(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        store, _ = plumbing
        refused = await api.post(
            f"{A}/imports/inspect", headers=headers, files={"file": ("chat.txt", CHAT.encode())}
        )
        settings = LearningSettingsRepository(db, tenant_id).ensure()
        settings.activo = True
        db.commit()

        response = await api.post(
            f"{A}/imports/inspect", headers=headers, files={"file": ("chat.txt", CHAT.encode())}
        )
        junk = await api.post(
            f"{A}/imports/inspect", headers=headers, files={"file": ("chat.txt", b"hola")}
        )

        assert refused.status_code == 409 and refused.json()["error"] == "learning_off"
        body = response.json()
        assert {p["nombre"]: p["mensajes"] for p in body["participantes"]} == {
            "Tienda Sol": 8, "Carla Ríos": 4, "Luis Soto": 3,
        }  # fmt: skip
        assert (
            body["audios"] == 1 and body["conversaciones"] == 2 and body["analisis_necesarios"] == 5
        )
        assert junk.status_code == 422 and junk.json()["error"] == "no_messages"
        assert store.values == {}

    async def test_handing_it_over_needs_the_authorisation_and_the_seller(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        LearningSettingsRepository(db, tenant_id).ensure().activo = True
        db.commit()
        files = {"file": ("chat.txt", CHAT.encode())}

        no_auth = await api.post(
            f"{A}/imports",
            headers=headers,
            files=files,
            data={"vendedores": '["Tienda Sol"]', "autorizo": "false"},
        )
        no_seller = await api.post(
            f"{A}/imports",
            headers=headers,
            files=files,
            data={"vendedores": "[]", "autorizo": "true"},
        )

        assert no_auth.status_code == 422 and no_auth.json()["error"] == "authorization_required"
        assert no_seller.status_code == 422 and no_seller.json()["error"] == "sellers_required"

    async def test_it_is_authorised_recorded_kept_for_the_worker_only_and_one_at_a_time(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        store, recording = plumbing
        LearningSettingsRepository(db, tenant_id).ensure().activo = True
        db.commit()
        data = {"vendedores": '["Tienda Sol"]', "autorizo": "true"}

        started = await api.post(
            f"{A}/imports", headers=headers, files={"file": ("chat.txt", CHAT.encode())}, data=data
        )
        second = await api.post(
            f"{A}/imports", headers=headers, files={"file": ("chat.txt", CHAT.encode())}, data=data
        )

        assert started.status_code == 200, started.text
        import_id = started.json()["id"]
        assert started.json()["estado"] == "procesando"
        assert list(store.values) == [
            f"chat-import:{import_id}",
            f"chat-import:{import_id}:sellers",
        ]
        assert all(ttl and ttl <= 2 * 3600 for ttl in store.ttls.values())
        assert recording.jobs == [("process_chat_import", (import_id, str(tenant_id)))]
        audit = db.execute(
            select(AuditLog).where(AuditLog.action == "learning.import_authorized")
        ).scalar_one()
        assert audit.detail == {"import_id": import_id}
        assert second.status_code == 409 and second.json()["error"] == "already_running"
        listing = await api.get(f"{A}/imports", headers=headers)
        assert [row["id"] for row in listing.json()["items"]] == [import_id]

    async def test_it_is_refused_when_the_week_does_not_have_the_analyses(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        settings = LearningSettingsRepository(db, tenant_id).ensure()
        settings.activo, settings.tope_semanal = True, 3
        db.commit()

        response = await api.post(
            f"{A}/imports",
            headers=headers,
            files={"file": ("chat.txt", CHAT.encode())},
            data={"vendedores": '["Tienda Sol"]', "autorizo": "true"},
        )

        assert response.status_code == 409 and response.json()["error"] == "budget"
        assert plumbing[0].values == {}

    async def test_a_broken_queue_leaves_no_original_behind(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        store, recording = plumbing
        recording.broken = True
        LearningSettingsRepository(db, tenant_id).ensure().activo = True
        db.commit()

        response = await api.post(
            f"{A}/imports",
            headers=headers,
            files={"file": ("chat.txt", CHAT.encode())},
            data={"vendedores": '["Tienda Sol"]', "autorizo": "true"},
        )

        assert response.status_code == 503 and store.values == {}
        row = db.execute(select(ChatImport)).scalar_one()
        assert row.estado == "fallido"

    async def test_another_businesss_import_is_a_404(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any
    ) -> None:
        _, mine = await _account(api, mail)
        theirs, _ = await _account(api, mail)
        record = ChatImportRepository(db, theirs).add()
        db.commit()

        response = await api.get(f"{A}/imports/{record.id}", headers=mine)
        listing = await api.get(f"{A}/imports", headers=mine)

        assert response.status_code == 404 and listing.json()["items"] == []

    async def test_examples_are_approved_together_listed_and_switched_off(
        self, api: AsyncClient, mail: Any, db: Session, plumbing: Any, queue: Any
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        tenant = db.get(__import__("app.models.tenant", fromlist=["Tenant"]).Tenant, tenant_id)
        LearningSettingsRepository(db, tenant_id).ensure().activo = True
        await run_import(
            db, ChatImportRepository(db, tenant_id).add(), CHAT.encode(), ["Tienda Sol"],
            llm=ScriptedModel(), transcriber=MockTranscriptionProvider(), actor=None,
        )  # fmt: skip
        db.commit()
        pending = (await api.get(f"{A}/suggestions", headers=headers)).json()["items"]
        example_ids = [row["id"] for row in pending if row["tipo"] == "ejemplo"]
        other = [row["id"] for row in pending if row["tipo"] == "situacion"]

        batch = await api.post(
            f"{A}/suggestions/approve-batch", headers=headers, json={"ids": [*example_ids, *other]}
        )
        examples = (await api.get(f"{A}/examples", headers=headers)).json()["items"]
        off = await api.put(
            f"{A}/examples/{examples[0]['id']}/estado", headers=headers, json={"activo": False}
        )
        gone = await api.delete(f"{A}/examples/{examples[1]['id']}", headers=headers)
        after = (await api.get(f"{A}/examples", headers=headers)).json()["items"]

        assert tenant is not None
        assert batch.json() == {"aprobadas": 2}  # the situation waits for a person
        assert len(examples) == 2 and {e["estado"] for e in examples} == {"activo"}
        assert off.json()["estado"] == "inactivo" and gone.status_code == 204
        assert [e["estado"] for e in after] == ["inactivo"]
        assert any(job[0] == "embed_situations" for job in queue.jobs)
        still_pending = (await api.get(f"{A}/suggestions", headers=headers)).json()["items"]
        assert [row["tipo"] for row in still_pending if row["tipo"] == "situacion"] == ["situacion"]
        assert db.execute(select(ExampleConversation)).scalars().all()
