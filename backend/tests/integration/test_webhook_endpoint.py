"""`POST /webhooks/ycloud/<token>` (TASK-203, one URL and one secret per number).

This URL is reachable by anyone on the internet and writes into the inbox of
every business on the platform, so what is pinned here is mostly what it
*refuses*: an unsigned delivery, a tampered body, a payload with no event id.

The other half is HU-01's promise. A delivery that gets past the signature is
stored before anything is understood about it, exactly once no matter how many
times the provider sends it, and the answer comes back inside two seconds
because nothing but the storing happens in the request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue
from app.models.channel import WebhookEvent
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.whatsapp.base import NumberCredentials
from app.providers.whatsapp.ycloud import YCloudProvider
from app.repositories.channel import WebhookEventRepository
from app.repositories.whatsapp import number_by_webhook_token
from app.services.connection import store_credentials
from tests.fixtures import load

# Every business has its own YCloud account, so its own URL and its own secret.
# The token is random per run because `webhook_token` is unique platform-wide
# and this suite shares a database with everything else.
TOKEN = uuid.uuid4().hex + uuid.uuid4().hex
WEBHOOK = f"/webhooks/ycloud/{TOKEN}"
SECRET = "whsec_the_suites_own_secret"


class RecordingQueue:
    """A `JobQueue` that remembers instead of reaching Redis."""

    def __init__(self, *, fail: bool = False) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []
        self._fail = fail

    async def enqueue(self, function: str, *args: object) -> str | None:
        if self._fail:
            raise RuntimeError("redis is down")
        self.enqueued.append((function, args))
        return f"job-{len(self.enqueued)}"


def _connect_number(db: Session, *, token: str, secret: str | None) -> WhatsAppNumber:
    """A tenant with a connected number whose webhook URL carries `token`."""
    tenant = Tenant(name=f"Webhook {uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    number = WhatsAppNumber(
        tenant_id=tenant.id,
        phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
        display_name="Panaderia Quispe",
        provider="ycloud",
        status="connected",
        webhook_token=token,
    )
    db.add(number)
    db.flush()
    values = {
        "api_key": "key_for_the_suite",
        "waba_id": "waba_suite",
        "phone_number_id": "pn_suite",
    }
    if secret is not None:
        values["webhook_secret"] = secret
    store_credentials(
        db,
        number,
        credentials=NumberCredentials(provider="ycloud", values=values),
        provider=YCloudProvider(),
    )
    db.commit()
    return number


@pytest.fixture(autouse=True)
def webhook_number(db: Session) -> WhatsAppNumber:
    """The number the suite's URL belongs to, with a secret the suite owns."""
    existing = number_by_webhook_token(db, TOKEN)
    return existing or _connect_number(db, token=TOKEN, secret=SECRET)


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recording
    return recording


def _delivery(payload: dict[str, Any] | None = None) -> tuple[bytes, dict[str, str]]:
    """A body and the headers that make it genuine.

    The event id is randomised per call because `provider_event_id` is unique
    platform-wide and this suite shares a database with everything else.
    """
    body = payload or load("ycloud/inbound_text.json")
    body = {**body, "id": f"evt_{uuid.uuid4().hex}"}
    raw = json.dumps(body).encode()
    timestamp = "1785000000"
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256)
    return raw, {
        "YCloud-Signature": f"t={timestamp},s={digest.hexdigest()}",
        "Content-Type": "application/json",
    }


class TestWhatItRefuses:
    async def test_an_unsigned_delivery_is_401(
        self, api: AsyncClient, queue: RecordingQueue
    ) -> None:
        raw, _ = _delivery()

        response = await api.post(
            WEBHOOK, content=raw, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_signature"
        assert queue.enqueued == []

    async def test_a_tampered_body_is_401(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """The realistic attack: a genuine capture replayed with the phone
        number swapped for one belonging to somebody else."""
        raw, headers = _delivery()
        tampered = raw.replace(b"+51987654321", b"+51900000000")

        response = await api.post(WEBHOOK, content=tampered, headers=headers)

        assert response.status_code == 401
        assert queue.enqueued == []

    async def test_nothing_is_stored_for_a_refused_delivery(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        raw, _ = _delivery()
        event_id = json.loads(raw)["id"]

        await api.post(WEBHOOK, content=raw, headers={"Content-Type": "application/json"})

        assert WebhookEventRepository(db).by_provider_event_id(event_id) is None

    async def test_a_body_that_is_not_json_is_400(
        self, api: AsyncClient, queue: RecordingQueue
    ) -> None:
        raw = b"<html>not json</html>"
        timestamp = "1785000000"
        digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256)

        response = await api.post(
            WEBHOOK,
            content=raw,
            headers={"YCloud-Signature": f"t={timestamp},s={digest.hexdigest()}"},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_an_event_without_an_id_is_400(
        self, api: AsyncClient, queue: RecordingQueue
    ) -> None:
        """No id means no idempotency, and storing it under an invented one
        would make the provider's own retry arrive as a second message."""
        payload = load("ycloud/inbound_text.json")
        del payload["id"]
        raw = json.dumps(payload).encode()
        timestamp = "1785000000"
        digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256)

        response = await api.post(
            WEBHOOK,
            content=raw,
            headers={"YCloud-Signature": f"t={timestamp},s={digest.hexdigest()}"},
        )

        assert response.status_code == 400
        assert queue.enqueued == []

    async def test_an_unknown_token_is_401_like_a_bad_signature(
        self, api: AsyncClient, queue: RecordingQueue
    ) -> None:
        """The same answer, so the response cannot be used to find out which
        tokens exist."""
        raw, headers = _delivery()

        unknown = await api.post("/webhooks/ycloud/not-a-real-token", content=raw, headers=headers)
        unsigned = await api.post(
            WEBHOOK, content=raw, headers={"Content-Type": "application/json"}
        )

        assert unknown.status_code == unsigned.status_code == 401
        assert unknown.json() == unsigned.json()
        assert queue.enqueued == []

    async def test_a_number_with_no_secret_refuses_everything(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """The correct failure. An endpoint with no secret is a public write
        API to a business's inbox."""
        token = uuid.uuid4().hex + uuid.uuid4().hex
        _connect_number(db, token=token, secret=None)
        raw, headers = _delivery()

        response = await api.post(f"/webhooks/ycloud/{token}", content=raw, headers=headers)

        assert response.status_code == 401
        assert queue.enqueued == []

    async def test_another_businesss_secret_does_not_open_this_url(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """Two businesses, two YCloud accounts, two secrets. A delivery signed
        with the other one's secret must not be accepted on this URL."""
        token = uuid.uuid4().hex + uuid.uuid4().hex
        _connect_number(db, token=token, secret="whsec_another_business")
        raw, headers = _delivery()  # signed with the suite's own SECRET

        response = await api.post(f"/webhooks/ycloud/{token}", content=raw, headers=headers)

        assert response.status_code == 401
        assert queue.enqueued == []

    async def test_a_disconnected_number_is_refused(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        number = _connect_number(db, token=token, secret=SECRET)
        number.status = "disconnected"
        db.commit()
        raw, headers = _delivery()

        response = await api.post(f"/webhooks/ycloud/{token}", content=raw, headers=headers)

        assert response.status_code == 401


class TestWhatItAccepts:
    async def test_a_genuine_delivery_is_stored_raw(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """Stored *before* anything is understood about it, which is what
        makes a parser fix replayable instead of a message lost."""
        raw, headers = _delivery()
        event_id = json.loads(raw)["id"]

        response = await api.post(WEBHOOK, content=raw, headers=headers)

        assert response.status_code == 200
        stored = WebhookEventRepository(db).by_provider_event_id(event_id)
        assert stored is not None
        assert stored.status == "received"
        assert stored.payload == json.loads(raw)

    async def test_the_work_is_handed_to_a_worker(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        raw, headers = _delivery()

        await api.post(WEBHOOK, content=raw, headers=headers)

        assert len(queue.enqueued) == 1
        function, args = queue.enqueued[0]
        assert function == "process_webhook_event"
        event_id = json.loads(raw)["id"]
        stored = WebhookEventRepository(db).by_provider_event_id(event_id)
        assert stored is not None
        assert args == (str(stored.id),)

    async def test_the_answer_says_nothing_about_the_tenant(
        self, api: AsyncClient, queue: RecordingQueue
    ) -> None:
        """A response that distinguished "number I know" from "number I do
        not" would let anybody enumerate the platform's customers one webhook
        at a time."""
        known, headers_known = _delivery()
        payload = load("ycloud/inbound_text.json")
        payload["whatsappInboundMessage"]["to"] = "+51900000000"
        unknown, headers_unknown = _delivery(payload)

        first = await api.post(WEBHOOK, content=known, headers=headers_known)
        second = await api.post(WEBHOOK, content=unknown, headers=headers_unknown)

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json() == {"received": True}


class TestIdempotency:
    async def test_the_same_delivery_three_times_is_stored_once(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """RF-002. The provider retries, and a retry is the same message -
        not a customer who asked twice."""
        raw, headers = _delivery()
        event_id = json.loads(raw)["id"]

        for _ in range(3):
            assert (await api.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

        stored = db.execute(
            select(WebhookEvent).where(WebhookEvent.provider_event_id == event_id)
        ).scalars()
        assert len(list(stored)) == 1

    async def test_a_redelivery_of_unfinished_work_is_queued_again(
        self, api: AsyncClient, queue: RecordingQueue
    ) -> None:
        """The first attempt stored the row and the worker has not finished
        it. The provider's retry is a second chance, not a duplicate: dropping
        it would strand the message in `received` forever."""
        raw, headers = _delivery()

        await api.post(WEBHOOK, content=raw, headers=headers)
        await api.post(WEBHOOK, content=raw, headers=headers)

        assert len(queue.enqueued) == 2

    async def test_a_redelivery_of_finished_work_is_not(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """Once processed, a retry must not re-run: that is how one customer
        gets answered twice."""
        raw, headers = _delivery()
        await api.post(WEBHOOK, content=raw, headers=headers)
        event_id = json.loads(raw)["id"]
        stored = WebhookEventRepository(db).by_provider_event_id(event_id)
        assert stored is not None
        stored.status = "processed"
        db.commit()

        response = await api.post(WEBHOOK, content=raw, headers=headers)

        assert response.status_code == 200
        assert len(queue.enqueued) == 1

    async def test_a_dead_lettered_event_is_not_requeued_by_a_retry(
        self, api: AsyncClient, queue: RecordingQueue, db: Session
    ) -> None:
        """It is waiting for a human, not for another automatic attempt."""
        raw, headers = _delivery()
        await api.post(WEBHOOK, content=raw, headers=headers)
        event_id = json.loads(raw)["id"]
        stored = WebhookEventRepository(db).by_provider_event_id(event_id)
        assert stored is not None
        stored.status = "dead_letter"
        db.commit()

        await api.post(WEBHOOK, content=raw, headers=headers)

        assert len(queue.enqueued) == 1


class TestWhenTheQueueIsDown:
    async def test_the_delivery_is_still_stored(
        self, api: AsyncClient, app: FastAPI, db: Session
    ) -> None:
        """Nothing is lost. The row is committed before the enqueue is even
        attempted, precisely so that the queue being down costs a delay rather
        than a customer's message."""
        broken = RecordingQueue(fail=True)
        app.dependency_overrides[get_job_queue] = lambda: broken
        raw, headers = _delivery()
        event_id = json.loads(raw)["id"]

        response = await api.post(WEBHOOK, content=raw, headers=headers)

        assert response.status_code == 503
        assert WebhookEventRepository(db).by_provider_event_id(event_id) is not None

    async def test_the_provider_is_asked_to_redeliver(self, api: AsyncClient, app: FastAPI) -> None:
        """503 rather than 200, so YCloud sends it again - and the redelivery
        finds the stored row still unfinished and queues it properly."""
        broken = RecordingQueue(fail=True)
        app.dependency_overrides[get_job_queue] = lambda: broken
        raw, headers = _delivery()

        response = await api.post(WEBHOOK, content=raw, headers=headers)

        assert response.status_code == 503
        assert response.json() == {"received": False}


class TestTheEndpointIsNotVersioned:
    async def test_it_is_not_under_the_api_prefix(self, api: AsyncClient) -> None:
        """The URL lives in a provider's console. A version bump that moved it
        would silently stop a customer's number from working."""
        assert (await api.post("/api/v1/webhooks/ycloud")).status_code == 404

    async def test_it_is_not_in_the_published_schema(self, app: FastAPI) -> None:
        """The generated client has no business calling it, and publishing the
        path only helps somebody looking for an unauthenticated endpoint."""
        assert WEBHOOK not in app.openapi()["paths"]
