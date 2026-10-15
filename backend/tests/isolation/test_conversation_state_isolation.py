"""A conversation's state and its handover never cross businesses (TASK-443).

A's operator asking for B's conversation gets a 404 on both endpoints, and B's
pause stays exactly as it was.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.conversation import Conversation
from app.models.conversation_state import FunnelCode
from app.process.service import ProcessConfigService
from app.process.state import StateRepo
from app.process.templates.base import Stage
from tests.isolation.test_payment_isolation import _owner

C = "/api/v1/tenant/conversations"


async def _tenant_id(api: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await api.get("/api/v1/tenant", headers=headers)).json()["id"])


def _handed_over(db: Session, tenant_id: uuid.UUID) -> Conversation:
    if db.query(AgentConfig).filter_by(tenant_id=tenant_id).one_or_none() is None:
        db.add(AgentConfig(tenant_id=tenant_id, agent_name="Leo", process_template_key="productos"))
        db.flush()
    conversation = Conversation(tenant_id=tenant_id, channel="sandbox", status="open")
    db.add(conversation)
    db.flush()
    config = ProcessConfigService(db).effective(tenant_id)
    states = StateRepo(db, tenant_id)
    state = states.get_or_create(conversation, config.template)
    states.save(
        conversation.id,
        stage=Stage.DERIVADO,
        funnel=FunnelCode(state.funnel_code),
        facts_patch={
            "saludo_enviado": True,
            "derivado": {
                "motivo": "queja",
                "resumen": "Reclamo de B",
                "hasta": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
            },
        },
        reason="test",
        expected_version=state.version,
    )
    return conversation


class TestOneBusinessNeverReachesAnothersConversation:
    async def test_state_and_resume_answer_404(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")
        tenant_b = await _tenant_id(api, b)
        conversation = _handed_over(db, tenant_b)

        assert (await api.get(f"{C}/{conversation.id}/state", headers=a)).status_code == 404
        assert (await api.post(f"{C}/{conversation.id}/resume-bot", headers=a)).status_code == 404

        state = StateRepo(db, tenant_b).get(conversation.id)
        assert state is not None and state.facts["derivado"]["resumen"] == "Reclamo de B"
        mine = await api.get(f"{C}/{conversation.id}/state", headers=b)
        assert mine.status_code == 200 and mine.json()["derivado"]["motivo"] == "queja"
