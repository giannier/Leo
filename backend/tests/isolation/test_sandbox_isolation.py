"""One business never reaches another's sandbox conversation (TASK-015)."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from app.api.deps import get_platform_llm, get_sandbox_limiter
from app.core.rate_limit import NullRateLimiter
from app.providers.llm import ChatResult, MockLLMProvider

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
SANDBOX = "/api/v1/tenant/sandbox"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


@pytest.fixture
def llm(app: Any) -> MockLLMProvider:
    provider = MockLLMProvider()
    app.dependency_overrides[get_platform_llm] = lambda: provider
    app.dependency_overrides[get_sandbox_limiter] = NullRateLimiter
    return provider


async def _ready_owner(api: AsyncClient, mail: Any, label: str) -> dict[str, str]:
    body = {
        "full_name": f"Owner {label}",
        "email": f"{label}-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": f"Negocio {label}",
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

    await api.put(
        f"{TENANT}/business-profile",
        headers=headers,
        json={"business_name": f"Negocio {label}", "industry": "restaurante"},
    )
    await api.put(f"{TENANT}/agent-config", headers=headers, json={"agent_name": f"Bot {label}"})
    await api.post(
        f"{TENANT}/products", headers=headers, json={"name": f"Producto {label}", "price": "10.00"}
    )
    await api.post(
        f"{TENANT}/payment-methods",
        headers=headers,
        json={"method": "yape", "instructions": "Yape al 999"},
    )
    return headers


class TestOneBusinessNeverReachesAnothersSandbox:
    async def test_a_foreign_conversation_cannot_be_read(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        headers_a = await _ready_owner(api, mail, "a")
        headers_b = await _ready_owner(api, mail, "b")

        conversation_b = (await api.post(f"{SANDBOX}/conversations", headers=headers_b)).json()[
            "conversation_id"
        ]
        response = await api.get(
            f"{SANDBOX}/conversations/{conversation_b}/messages", headers=headers_a
        )
        assert response.status_code == 404

    async def test_a_foreign_conversation_cannot_be_written_to(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        """The nastiest shape: injecting a message into somebody else's
        transcript, which their operator would read as a real customer."""
        headers_a = await _ready_owner(api, mail, "a")
        headers_b = await _ready_owner(api, mail, "b")

        conversation_b = (await api.post(f"{SANDBOX}/conversations", headers=headers_b)).json()[
            "conversation_id"
        ]
        response = await api.post(
            f"{SANDBOX}/conversations/{conversation_b}/messages",
            headers=headers_a,
            json={"content": "mensaje inyectado"},
        )
        assert response.status_code == 404

        history = (
            await api.get(f"{SANDBOX}/conversations/{conversation_b}/messages", headers=headers_b)
        ).json()
        assert history["total"] == 0

    async def test_a_foreign_id_is_indistinguishable_from_a_missing_one(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        headers_a = await _ready_owner(api, mail, "a")
        headers_b = await _ready_owner(api, mail, "b")

        conversation_b = (await api.post(f"{SANDBOX}/conversations", headers=headers_b)).json()[
            "conversation_id"
        ]
        foreign = await api.get(
            f"{SANDBOX}/conversations/{conversation_b}/messages", headers=headers_a
        )
        invented = await api.get(
            f"{SANDBOX}/conversations/{uuid.uuid4()}/messages", headers=headers_a
        )
        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()

    async def test_each_agent_answers_as_its_own_business(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        """The prompt is built per tenant, so A's agent never introduces
        itself as B."""
        headers_a = await _ready_owner(api, mail, "a")
        conversation_a = (await api.post(f"{SANDBOX}/conversations", headers=headers_a)).json()[
            "conversation_id"
        ]
        llm.queue(ChatResult(content="hola"))
        await api.post(
            f"{SANDBOX}/conversations/{conversation_a}/messages",
            headers=headers_a,
            json={"content": "hola"},
        )
        system = llm.chat_calls[0].messages[0].content
        assert "Negocio a" in system or "Bot a" in system
        assert "Negocio b" not in system
