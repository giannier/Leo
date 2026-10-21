"""Capture and qualify leads: tool, score, hot-lead notice (019, phase E)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.leads import calificar_cliente
from app.agent.tools.registry import ToolContext
from app.models.tenant import HOT_LEAD, TenantNotification
from app.process.facts import Facts, LeadCalificado
from app.process.templates.base import Stage
from app.providers.llm import MockLLMProvider
from app.services.crm import SCORE_BY_INTEREST
from app.services.tenant_alerts import TenantAlerts
from tests.integration.test_crm_api import L, _lead, _setup
from tests.integration.test_crm_leads import _leads, _save
from tests.integration.test_crm_leads import _setup as _lead_setup
from tests.integration.test_turn_pipeline import _conversation, _tenant


def _qualified(
    interest: str, *, when: datetime | None = None, **extra: object
) -> dict[str, object]:
    return {
        "interes": interest,
        "motivo": "Necesita el sistema para marzo y pide una propuesta",
        "respuestas": {"¿Para cuándo lo necesitas?": "marzo", "¿Cuántas personas?": "20"},
        "en": (when or datetime.now(UTC)).isoformat(),
        **extra,
    }


class TestTheLeadIsScored:
    @pytest.mark.parametrize(
        ("interest", "score"), [("alto", "caliente"), ("medio", "tibio"), ("bajo", "frio")]
    )
    def test_the_assistant_s_judgement_becomes_the_leads_score(
        self, db: Session, interest: str, score: str
    ) -> None:
        tenant, conversation, states = _lead_setup(db)

        _save(states, conversation, Stage.IDENTIFICAR, lead_calificado=_qualified(interest))

        (lead,) = _leads(db, tenant)
        assert lead.score == score == SCORE_BY_INTEREST[interest]
        assert lead.qualification == {
            "¿Para cuándo lo necesitas?": "marzo",
            "¿Cuántas personas?": "20",
        }
        assert lead.score_reason and lead.scored_at is not None

    def test_a_qualified_customer_is_a_lead_before_any_order(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)

        _save(states, conversation, Stage.INICIO, lead_calificado=_qualified("bajo"))
        (lead,) = _leads(db, tenant)

        assert lead.score == "frio"  # a lead on the board, still cold

    def test_a_newer_judgement_replaces_the_score_and_an_old_one_does_not(
        self, db: Session
    ) -> None:
        tenant, conversation, states = _lead_setup(db)
        first = datetime.now(UTC)
        _save(
            states, conversation, Stage.IDENTIFICAR, lead_calificado=_qualified("medio", when=first)
        )
        (lead,) = _leads(db, tenant)

        _save(
            states,
            conversation,
            Stage.IDENTIFICAR,
            lead_calificado=_qualified("alto", when=first + timedelta(minutes=5)),
        )
        db.refresh(lead)
        assert lead.score == "caliente"

        _save(
            states,
            conversation,
            Stage.IDENTIFICAR,
            lead_calificado=_qualified("bajo", when=first - timedelta(minutes=5)),
        )
        db.refresh(lead)
        assert lead.score == "caliente"


class TestTheTool:
    def _context(self, db: Session, notices: Any = None, **facts: Any) -> ToolContext:
        tenant = _tenant(db, template="servicios")
        conversation = _conversation(db, tenant, channel="whatsapp")
        return ToolContext(
            session=db,
            tenant_id=tenant.id,
            llm=MockLLMProvider(),
            conversation_id=conversation.id,
            notices=notices,
            facts=Facts(**facts),
        )

    async def test_it_stores_the_answers_and_says_nothing_to_the_customer(
        self, db: Session
    ) -> None:
        context = self._context(db)

        result = await calificar_cliente(
            context,
            {
                "interes": "medio",
                "motivo": "Quiere una web, sin fecha",
                "respuestas": [
                    {"pregunta": "¿Para cuándo?", "respuesta": "no sabe"},
                    {"pregunta": "¿Para cuándo?", "respuesta": "repetida"},
                    {"pregunta": "", "respuesta": "x"},
                    "no es un dato",
                ],
            },
        )

        stored = context.facts_patch["lead_calificado"]
        assert result.status == "hecho" and "no se la menciones" in (result.decir or "")
        assert stored["interes"] == "medio" and stored["respuestas"] == {"¿Para cuándo?": "no sabe"}

    async def test_an_invented_level_is_an_error(self, db: Session) -> None:
        result = await calificar_cliente(
            self._context(db), {"interes": "altísimo", "respuestas": []}
        )

        assert result.status == "error"

    async def test_the_team_hears_the_first_time_a_customer_is_hot_and_not_again(
        self, db: Session
    ) -> None:
        calls: list[str | None] = []

        class Notices:
            def hot_lead(self, conversation_id: uuid.UUID, *, resumen: str | None) -> None:
                calls.append(resumen)

        context = self._context(db, notices=Notices())
        await calificar_cliente(
            context, {"interes": "alto", "motivo": "pide propuesta", "respuestas": []}
        )
        again = self._context(db, notices=Notices(), lead_calificado=LeadCalificado(interes="alto"))
        await calificar_cliente(again, {"interes": "alto", "motivo": "otra vez", "respuestas": []})
        warm = self._context(db, notices=Notices())
        await calificar_cliente(warm, {"interes": "medio", "motivo": "tibio", "respuestas": []})

        assert calls == ["pide propuesta"]

    async def test_a_failing_notice_never_fails_the_conversation(self, db: Session) -> None:
        class Broken:
            def hot_lead(self, conversation_id: uuid.UUID, *, resumen: str | None) -> None:
                raise RuntimeError("smtp down")

        result = await calificar_cliente(
            self._context(db, notices=Broken()), {"interes": "alto", "respuestas": []}
        )

        assert result.status == "hecho"


class TestTheHotLeadNotice:
    def test_it_is_recorded_for_the_business(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        conversation = _conversation(db, tenant, channel="whatsapp")

        TenantAlerts(db, tenant.id).hot_lead(conversation.id, resumen="Pide propuesta para marzo")

        (notice,) = db.execute(
            select(TenantNotification).where(
                TenantNotification.tenant_id == tenant.id, TenantNotification.kind == HOT_LEAD
            )
        ).scalars()
        assert notice.payload["resumen"] == "Pide propuesta para marzo"


class TestTheScoreInTheApi:
    async def test_the_lead_carries_its_score_and_the_board_filters_by_it(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, owner, _ = await _setup(api, mail, db)
        hot = _lead(db, tenant_id, name="Caliente")
        _lead(db, tenant_id, name="Sin puntaje")
        from app.models.crm import Lead

        lead = db.execute(select(Lead).where(Lead.conversation_id == hot.id)).scalar_one()
        lead.score, lead.score_reason = "caliente", "Pide propuesta"
        lead.qualification = {"¿Para cuándo?": "marzo"}
        db.commit()

        everyone = (await api.get(L, headers=owner)).json()["items"]
        only_hot = (await api.get(L, headers=owner, params={"puntaje": "caliente"})).json()["items"]
        detail = (await api.get(f"{L}/{lead.id}", headers=owner)).json()["lead"]
        none = (await api.get(L, headers=owner, params={"puntaje": "frio"})).json()["items"]

        assert len(everyone) == 2 and [i["nombre"] for i in only_hot] == ["Caliente"] and none == []
        assert detail["calificacion"]["puntaje"] == "caliente"
        assert detail["calificacion"]["respuestas"] == {"¿Para cuándo?": "marzo"}
        assert all(i["calificacion"] is None for i in everyone if i["nombre"] != "Caliente")


class TestWhatTheAgentAsks:
    async def test_the_owner_s_questions_replace_the_rubro_s_and_empty_goes_back(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        from tests.integration.test_assistant_config_api import TENANT, _account

        _, headers = await _account(api, mail)
        await api.put(f"{TENANT}/business-profile", headers=headers, json={"industry": "servicios"})

        rubro = (await api.get("/api/v1/assistant/rubro", headers=headers)).json()
        mine = await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={"preguntas_calificacion": ["¿Qué sistema necesitas?", "¿Para cuándo?"]},
        )
        too_many = await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={"preguntas_calificacion": [f"Pregunta {i}" for i in range(6)]},
        )
        reset = await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"preguntas_calificacion": []}
        )

        assert len(rubro["preguntas_calificacion"]) == 3  # the rubro's own suggestion
        assert mine.json()["preguntas_calificacion"] == ["¿Qué sistema necesitas?", "¿Para cuándo?"]
        assert too_many.status_code == 400
        assert reset.json()["preguntas_calificacion"] is None

    async def test_the_prompt_asks_the_owner_s_questions_only_when_the_business_qualifies(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        from datetime import UTC, datetime

        from app.agent.prompts.stage import build_stage_prompt
        from app.process.service import ProcessConfigCache, ProcessConfigService
        from tests.integration.test_assistant_config_api import TENANT, _account

        tenant_id, headers = await _account(api, mail)
        await api.put(f"{TENANT}/business-profile", headers=headers, json={"industry": "servicios"})

        def prompt() -> str:
            config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant_id)
            return build_stage_prompt(
                config, Facts(), Stage.IDENTIFICAR, now=datetime.now(UTC)
            ).text

        assert "CAPTACIÓN" not in prompt()

        await api.put(f"{TENANT}/agent-config", headers=headers, json={"capacidades": ["captar"]})
        db.expire_all()
        generic = prompt()
        await api.put(
            f"{TENANT}/agent-config",
            headers=headers,
            json={"preguntas_calificacion": ["¿Qué sistema necesitas?"]},
        )
        db.expire_all()
        mine = prompt()

        assert "CAPTACIÓN" in generic and "¿Qué necesitas resolver?" in generic  # the rubro's
        assert "¿Qué sistema necesitas?" in mine and "¿Qué necesitas resolver?" not in mine
