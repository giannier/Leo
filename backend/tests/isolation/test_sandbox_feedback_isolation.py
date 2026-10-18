"""A simulator rating never crosses businesses (Feature 018, TASK-1804)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_sandbox import SANDBOX, _ready_owner
from tests.integration.test_sandbox_simulator import llm  # noqa: F401


async def test_another_business_cannot_rate_read_or_remove(
    api: AsyncClient,
    mail: Any,
    llm: MockLLMProvider,  # noqa: F811
) -> None:
    a = await _ready_owner(api, mail)
    b = await _ready_owner(api, mail)
    conversation = (await api.post(f"{SANDBOX}/conversations", headers=b)).json()["conversation_id"]
    llm.queue(ChatResult(content="Hola"))
    sent = await api.post(
        f"{SANDBOX}/conversations/{conversation}/messages", headers=b, json={"content": "hola"}
    )
    reply = sent.json()["salientes"][-1]["message_id"]
    url = f"{SANDBOX}/conversations/{conversation}/messages/{reply}/feedback"
    assert (
        await api.put(url, headers=b, json={"rating": -1, "motivo": "secreto"})
    ).status_code == 200

    assert (await api.put(url, headers=a, json={"rating": 1})).status_code == 404
    assert (await api.delete(url, headers=a)).status_code == 404
    history = await api.get(f"{SANDBOX}/conversations/{conversation}/messages", headers=a)
    assert history.status_code == 404
    mine = await api.get(f"{SANDBOX}/conversations/{conversation}/messages", headers=b)
    assert [m["valoracion"] for m in mine.json()["items"] if m["valoracion"]] == [
        {"rating": -1, "motivo": "secreto"}
    ]
