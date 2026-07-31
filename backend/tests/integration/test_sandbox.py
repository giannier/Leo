"""The sandbox, end to end (TASK-015).

The task's Verification asks for a full conversation against a real fixture
catalogue, which is what `test_a_full_conversation_quotes_the_real_catalogue`
does: wizard, product, agent, and an answer carrying the price that is
actually in the database.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_platform_llm, get_sandbox_limiter
from app.core.rate_limit import NullRateLimiter, RateLimitDecision
from app.models.tenant import Tenant
from app.providers.llm import ChatResult, MockLLMProvider, ToolCall

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


def _tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=name, arguments=dict(arguments))


@pytest.fixture
def llm(app: Any) -> MockLLMProvider:
    """A scripted model, so these test the endpoints and not a vendor."""
    provider = MockLLMProvider()
    app.dependency_overrides[get_platform_llm] = lambda: provider
    app.dependency_overrides[get_sandbox_limiter] = NullRateLimiter
    return provider


async def _owner(api: AsyncClient, mail: Any) -> dict[str, str]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "business_name": "Panaderia Quispe",
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


async def _ready_owner(api: AsyncClient, mail: Any) -> dict[str, str]:
    """An owner whose checklist has reached the sandbox threshold."""
    headers = await _owner(api, mail)
    await api.put(
        f"{TENANT}/business-profile",
        headers=headers,
        json={"business_name": "Panaderia Quispe", "industry": "restaurante"},
    )
    await api.put(
        f"{TENANT}/agent-config",
        headers=headers,
        json={"agent_name": "Leo", "tone": "cercano", "objective": "vender"},
    )
    await api.post(
        f"{TENANT}/products",
        headers=headers,
        json={"name": "Lomo saltado", "price": "32.00", "category": "Platos"},
    )
    await api.post(
        f"{TENANT}/payment-methods",
        headers=headers,
        json={"method": "yape", "instructions": "Yape al 999888777"},
    )
    return headers


class TestGuard:
    async def test_an_incomplete_checklist_cannot_open_a_conversation(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        headers = await _owner(api, mail)
        response = await api.post(f"{SANDBOX}/conversations", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"] == "sandbox_not_ready"

    async def test_the_guard_and_the_checklist_agree(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        """Same function behind both, so "start chatting" is never offered
        next to a call that refuses."""
        headers = await _owner(api, mail)
        checklist = (await api.get(f"{TENANT}/activation-checklist", headers=headers)).json()
        opened = await api.post(f"{SANDBOX}/conversations", headers=headers)

        assert checklist["sandbox_ready"] is False
        assert opened.status_code == 409

    async def test_a_ready_tenant_can_open_one(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        headers = await _ready_owner(api, mail)
        response = await api.post(f"{SANDBOX}/conversations", headers=headers)
        assert response.status_code == 201
        assert response.json()["conversation_id"]

    async def test_anonymous_callers_are_refused(self, api: AsyncClient) -> None:
        assert (await api.post(f"{SANDBOX}/conversations")).status_code == 401


class TestConversation:
    async def test_a_full_conversation_quotes_the_real_catalogue(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        """The task's Verification: a real conversation against the fixture
        catalogue, with the price that is actually in the database."""
        headers = await _ready_owner(api, mail)
        conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
            "conversation_id"
        ]

        llm.queue(
            ChatResult(content="", tool_calls=(_tool_call("consultar_catalogo", consulta="lomo"),))
        )
        llm.queue(
            ChatResult(
                content="El lomo saltado cuesta S/ 32.00. ¿Te lo preparo?",
                model="test-model",
                input_tokens=812,
                output_tokens=96,
            )
        )

        response = await api.post(
            f"{SANDBOX}/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "hola, cuanto cuesta el lomo saltado?"},
        )
        assert response.status_code == 200
        body = response.json()

        assert "32.00" in body["reply"]["content"]
        assert body["trace"]["model"] == "test-model"
        assert body["trace"]["tools_called"][0]["tool"] == "consultar_catalogo"
        assert body["trace"]["input_tokens"] == 812
        assert body["trace"]["latency_ms"] >= 0

        # The tool really read the tenant's own product row.
        tool_message = next(m for m in llm.chat_calls[1].messages if m.role == "tool")
        assert "Lomo saltado" in tool_message.content

    async def test_the_transcript_records_both_sides(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        headers = await _ready_owner(api, mail)
        conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
            "conversation_id"
        ]

        llm.queue(ChatResult(content="¡Hola! ¿En qué te ayudo?"))
        await api.post(
            f"{SANDBOX}/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "hola"},
        )

        history = (
            await api.get(f"{SANDBOX}/conversations/{conversation_id}/messages", headers=headers)
        ).json()
        assert history["total"] == 2
        assert [m["author"] for m in history["items"]] == ["customer", "agent"]

    async def test_the_question_survives_a_provider_failure(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        """The customer's message is persisted before the model is called.
        Losing the question along with the answer would make the transcript
        lie about what happened."""
        headers = await _ready_owner(api, mail)
        conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
            "conversation_id"
        ]

        # No responses queued: the mock raises, and the agent degrades.
        response = await api.post(
            f"{SANDBOX}/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "tienen delivery?"},
        )
        assert response.status_code == 200
        assert response.json()["trace"]["degraded"] is True

        history = (
            await api.get(f"{SANDBOX}/conversations/{conversation_id}/messages", headers=headers)
        ).json()
        assert any(m["content"] == "tienen delivery?" for m in history["items"])

    async def test_an_unknown_conversation_is_404(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider
    ) -> None:
        headers = await _ready_owner(api, mail)
        response = await api.post(
            f"{SANDBOX}/conversations/{uuid.uuid4()}/messages",
            headers=headers,
            json={"content": "hola"},
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("content", ["", "   ", "x" * 5000])
    async def test_malformed_messages_are_rejected(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, content: str
    ) -> None:
        headers = await _ready_owner(api, mail)
        conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
            "conversation_id"
        ]
        response = await api.post(
            f"{SANDBOX}/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": content},
        )
        assert response.status_code == 400


class TestQuota:
    async def test_the_sandbox_never_consumes_the_trial_quota(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        """Clarification 002. Charging for the place people evaluate the
        product suppresses the experimentation that leads to a sale."""
        headers = await _ready_owner(api, mail)
        conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
            "conversation_id"
        ]

        for _ in range(3):
            llm.queue(ChatResult(content="ok"))
            await api.post(
                f"{SANDBOX}/conversations/{conversation_id}/messages",
                headers=headers,
                json={"content": "hola"},
            )

        tenant_id = uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"])
        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        db.refresh(tenant)
        assert tenant.ai_messages_used == 0


class TestRateLimit:
    async def test_the_limit_is_per_tenant(
        self, api: AsyncClient, mail: Any, app: Any, llm: MockLLMProvider
    ) -> None:
        """Per tenant, not per user: the cost being guarded is the LLM bill,
        which the business incurs however many of its people are typing."""
        headers = await _ready_owner(api, mail)
        conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
            "conversation_id"
        ]

        seen: list[str] = []

        class RecordingLimiter:
            async def hit(self, key: str) -> RateLimitDecision:
                seen.append(key)
                return RateLimitDecision(allowed=False, retry_after_seconds=60)

        app.dependency_overrides[get_sandbox_limiter] = RecordingLimiter

        response = await api.post(
            f"{SANDBOX}/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "hola"},
        )
        assert response.status_code == 429
        assert response.json()["error"] == "rate_limited"
        assert response.headers["Retry-After"] == "60"
        assert seen[0].startswith("sandbox:")
