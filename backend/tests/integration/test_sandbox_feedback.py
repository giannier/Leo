"""¿Respondió bien? in the simulator (Feature 018, TASK-1804, HU-06)."""

# ruff: noqa: F811

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feedback import SandboxFeedback
from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_sandbox import SANDBOX, _ready_owner
from tests.integration.test_sandbox_simulator import llm  # noqa: F401


async def _a_reply(
    api: AsyncClient, headers: dict[str, str], llm: MockLLMProvider
) -> tuple[str, str, str]:  # noqa: F811
    conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
        "conversation_id"
    ]
    llm.queue(ChatResult(content="El certificado cuesta S/ 120."))
    sent = await api.post(
        f"{SANDBOX}/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "¿Cuánto cuesta el de SIAF?"},
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    return conversation_id, body["message_id"], body["salientes"][-1]["message_id"]


def _url(conversation_id: str, message_id: str) -> str:
    return f"{SANDBOX}/conversations/{conversation_id}/messages/{message_id}/feedback"


class TestRating:
    async def test_rate_change_and_read_back_in_the_history(
        self,
        api: AsyncClient,
        mail: Any,
        llm: MockLLMProvider,  # noqa: F811
    ) -> None:
        headers = await _ready_owner(api, mail)
        conversation_id, _, reply_id = await _a_reply(api, headers, llm)

        down = await api.put(
            _url(conversation_id, reply_id),
            headers=headers,
            json={"rating": -1, "motivo": "Debió ofrecer el paquete de 5"},
        )
        assert down.status_code == 200
        assert down.json() == {"rating": -1, "motivo": "Debió ofrecer el paquete de 5"}
        up = await api.put(_url(conversation_id, reply_id), headers=headers, json={"rating": 1})
        assert up.json() == {"rating": 1, "motivo": None}

        history = await api.get(
            f"{SANDBOX}/conversations/{conversation_id}/messages", headers=headers
        )
        rated = {m["id"]: m["valoracion"] for m in history.json()["items"]}
        assert rated[reply_id] == {"rating": 1, "motivo": None}

        removed = await api.delete(_url(conversation_id, reply_id), headers=headers)
        assert removed.status_code == 204
        again = await api.delete(_url(conversation_id, reply_id), headers=headers)
        assert again.status_code == 404

    async def test_only_the_assistant_s_replies_and_valid_values(
        self,
        api: AsyncClient,
        mail: Any,
        llm: MockLLMProvider,  # noqa: F811
    ) -> None:
        headers = await _ready_owner(api, mail)
        conversation_id, customer_id, reply_id = await _a_reply(api, headers, llm)

        own = await api.put(_url(conversation_id, customer_id), headers=headers, json={"rating": 1})
        assert own.status_code == 422 and own.json()["error"] == "not_a_reply"
        zero = await api.put(_url(conversation_id, reply_id), headers=headers, json={"rating": 0})
        assert zero.status_code == 400
        long = await api.put(
            _url(conversation_id, reply_id),
            headers=headers,
            json={"rating": -1, "motivo": "x" * 501},
        )
        assert long.status_code == 400
        unknown = await api.put(
            _url(conversation_id, str(uuid.uuid4())), headers=headers, json={"rating": 1}
        )
        assert unknown.status_code == 404

    async def test_the_rating_survives_reiniciar_chat_with_its_texts(
        self,
        api: AsyncClient,
        mail: Any,
        llm: MockLLMProvider,
        db: Session,  # noqa: F811
    ) -> None:
        headers = await _ready_owner(api, mail)
        conversation_id, _, reply_id = await _a_reply(api, headers, llm)
        await api.put(
            _url(conversation_id, reply_id), headers=headers, json={"rating": -1, "motivo": "No"}
        )

        reset = await api.post(f"{SANDBOX}/conversations/{conversation_id}/reset", headers=headers)
        assert reset.status_code == 204

        row = db.execute(select(SandboxFeedback).where(SandboxFeedback.motivo == "No")).scalar_one()
        # The conversation stays (only its messages go); the reply is gone.
        assert row.message_id is None
        assert str(row.conversation_id) == conversation_id
        assert row.pregunta == "¿Cuánto cuesta el de SIAF?"
        assert row.respuesta == "El certificado cuesta S/ 120."
        assert row.rating == -1
