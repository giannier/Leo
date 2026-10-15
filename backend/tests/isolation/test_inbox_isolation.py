"""The inbox never crosses businesses (Feature 007).

A's operator gets 404 on every endpoint with B's conversation id, A's list and A's
search never show B's conversations (even when B's message matches the words), and
A cannot touch B's notification settings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Conversation
from app.repositories.conversation import MessageRepository
from tests.isolation.test_payment_isolation import _owner

C = "/api/v1/tenant/conversations"


async def _tenant_id(api: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await api.get("/api/v1/tenant", headers=headers)).json()["id"])


def _thread(db: Session, tenant_id: uuid.UUID, said: str) -> Conversation:
    contact = Contact(
        tenant_id=tenant_id,
        phone_e164=f"+5197{uuid.uuid4().int % 10**7:07d}",
        profile_name="Cliente de B",
        last_inbound_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(tenant_id=tenant_id, channel="whatsapp", contact_id=contact.id)
    db.add(conversation)
    db.flush()
    MessageRepository(db, tenant_id).append(
        conversation_id=conversation.id,
        direction="inbound",
        author="customer",
        content=said,
        provider_message_id=f"wamid.{uuid.uuid4().hex}",
    )
    return conversation


class TestOneBusinessNeverReachesAnothersInbox:
    async def test_every_endpoint_answers_404(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")
        theirs = _thread(db, await _tenant_id(api, b), "secreto de B")
        base = f"{C}/{theirs.id}"

        calls = [
            ("get", base, None),
            ("get", f"{base}/messages", None),
            ("get", f"{base}/notes", None),
            ("post", f"{base}/notes", {"texto": "hola"}),
            ("post", f"{base}/takeover", None),
            ("post", f"{base}/release", None),
            ("post", f"{base}/steal", None),
            ("post", f"{base}/messages", {"texto": "hola"}),
            ("post", f"{base}/close", None),
            ("post", f"{base}/reopen", None),
            ("post", f"{base}/opt-out", None),
            ("get", f"{base}/messages/{uuid.uuid4()}/image", None),
        ]
        for method, url, body in calls:
            response = await getattr(api, method)(
                url, headers=a, **({"json": body} if body else {})
            )
            assert response.status_code == 404, (method, url, response.status_code)

        db.refresh(theirs)
        assert theirs.takeover_user_id is None and theirs.status == "open"
        assert theirs.unread_count == 1

    async def test_the_list_and_the_search_show_only_your_own(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")
        mine = _thread(db, await _tenant_id(api, a), "quiero una mochila")
        theirs = _thread(db, await _tenant_id(api, b), "quiero una mochila")

        listed = (await api.get(C, headers=a)).json()["items"]
        searched = (await api.get(f"{C}?q=mochila", headers=a)).json()["items"]

        assert [i["id"] for i in listed] == [str(mine.id)]
        assert [i["id"] for i in searched] == [str(mine.id)]
        assert str(theirs.id) not in {i["id"] for i in listed + searched}

    async def test_settings_are_per_business(self, api: AsyncClient, mail: Any) -> None:
        a = await _owner(api, mail, "a")
        b = await _owner(api, mail, "b")
        await api.put(
            "/api/v1/tenant/notification-settings",
            headers=b,
            json={"unattended_queue_minutes": 99, "auto_return_minutes": None},
        )

        mine = (await api.get("/api/v1/tenant/notification-settings", headers=a)).json()

        assert mine == {"unattended_queue_minutes": 10, "auto_return_minutes": 30}
