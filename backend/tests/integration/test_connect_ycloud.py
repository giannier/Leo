"""`POST /tenant/whatsapp-number`: a business connects its own YCloud account by itself.

The whole point is that nobody - not the platform's owner either - has a special
path: any business pastes its API key, and Leo checks it, creates the webhook in
that business's account and starts accepting its deliveries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_whatsapp_provider
from app.providers.whatsapp import YCloudProvider
from app.repositories.whatsapp import number_by_webhook_token
from app.services.connection import read_credentials
from tests.fixtures import load
from tests.integration.test_connection_requests import TENANT, _owner, _unique_number

KEY = "ycloud_key_0123456789"
SECRET = "whsec_returned_by_ycloud"


class YCloudStub:
    """What YCloud answers, and what it was asked."""

    def __init__(self, phone: str, *, key_ok: bool = True, secret: str | None = SECRET) -> None:
        self.phone = phone
        self.key_ok = key_ok
        self.secret = secret
        self.created_urls: list[str] = []
        self.deleted: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.key_ok:
            return httpx.Response(401, json={})
        path = request.url.path
        if request.method == "GET" and path.endswith("/whatsapp/phoneNumbers"):
            return httpx.Response(
                200,
                json={
                    "items": [{"phoneNumber": self.phone, "wabaId": "waba_1", "id": "pn_1_0123"}]
                },
            )
        if request.method == "POST" and path.endswith("/webhookEndpoints"):
            self.created_urls.append(json.loads(request.content)["url"])
            body: dict[str, Any] = {"id": "we_1"}
            if self.secret:
                body["secret"] = self.secret
            return httpx.Response(200, json=body)
        if request.method == "DELETE":
            self.deleted.append(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})


def _use(app: FastAPI, stub: YCloudStub) -> None:
    provider = YCloudProvider(transport=httpx.MockTransport(stub.handler))
    app.dependency_overrides[get_whatsapp_provider] = lambda: provider


def _body(phone: str, **overrides: Any) -> dict[str, Any]:
    body = {"api_key": KEY, "phone_e164": phone, "display_name": "Panaderia Leo"}
    body.update(overrides)
    return body


class TestConnecting:
    async def test_a_business_connects_with_only_its_api_key(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        phone = _unique_number()
        stub = YCloudStub(phone)
        _use(app, stub)

        response = await api.post(f"{TENANT}/whatsapp-number", headers=headers, json=_body(phone))

        assert response.status_code == 201
        assert response.json()["phone_e164"] == phone
        assert KEY not in response.text and SECRET not in response.text
        # The webhook was created in the business's account, pointing at Leo.
        token = stub.created_urls[0].rsplit("/", 1)[-1]
        assert stub.created_urls[0].endswith(f"/webhooks/ycloud/{token}")
        number = number_by_webhook_token(db, token)
        assert number is not None and number.phone_e164 == phone
        credentials = read_credentials(number)
        assert credentials is not None
        assert credentials.values["webhook_secret"] == SECRET
        assert credentials.values["waba_id"] == "waba_1"
        # ...and it is what the panel now shows.
        shown = await api.get(f"{TENANT}/whatsapp-number", headers=headers)
        assert shown.status_code == 200

    async def test_the_created_webhook_accepts_that_accounts_deliveries(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        """The loop that matters: connect, then a delivery signed with the secret
        YCloud returned is accepted on the URL Leo registered, and one signed with
        anything else is not."""
        recorded: list[Any] = []

        class Queue:
            async def enqueue(self, function: str, *args: object) -> str:
                recorded.append((function, args))
                return "job"

        app.dependency_overrides[get_job_queue] = lambda: Queue()
        headers, _ = await _owner(api, mail, db)
        phone = _unique_number()
        stub = YCloudStub(phone)
        _use(app, stub)
        await api.post(f"{TENANT}/whatsapp-number", headers=headers, json=_body(phone))
        path = "/webhooks/ycloud/" + stub.created_urls[0].rsplit("/", 1)[-1]

        payload = {**load("ycloud/inbound_text.json"), "id": f"evt_{uuid.uuid4().hex}"}
        raw = json.dumps(payload).encode()

        def signed(secret: str) -> dict[str, str]:
            digest = hmac.new(secret.encode(), b"1785000000." + raw, hashlib.sha256)
            return {"YCloud-Signature": f"t=1785000000,s={digest.hexdigest()}"}

        assert (await api.post(path, content=raw, headers=signed("whsec_other"))).status_code == 401
        assert (await api.post(path, content=raw, headers=signed(SECRET))).status_code == 200
        assert len(recorded) == 1


class TestRefusals:
    async def test_a_key_youtube_rejects_is_400(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        phone = _unique_number()
        _use(app, YCloudStub(phone, key_ok=False))

        response = await api.post(f"{TENANT}/whatsapp-number", headers=headers, json=_body(phone))

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_api_key"

    async def test_a_number_the_account_does_not_hold_is_400(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        _use(app, YCloudStub(_unique_number()))

        response = await api.post(
            f"{TENANT}/whatsapp-number", headers=headers, json=_body(_unique_number())
        )

        assert response.status_code == 400
        assert response.json()["error"] == "number_not_in_account"

    async def test_an_unverified_phone_cannot_connect(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db, phone_verified=False)
        phone = _unique_number()
        _use(app, YCloudStub(phone))

        response = await api.post(f"{TENANT}/whatsapp-number", headers=headers, json=_body(phone))

        assert response.status_code == 403
        assert response.json()["error"] == "phone_not_verified"

    async def test_a_number_already_connected_elsewhere_is_409_and_creates_nothing(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        first, _ = await _owner(api, mail, db)
        second, _ = await _owner(api, mail, db)
        phone = _unique_number()
        stub = YCloudStub(phone)
        _use(app, stub)
        assert (
            await api.post(f"{TENANT}/whatsapp-number", headers=first, json=_body(phone))
        ).status_code == 201
        created_before = len(stub.created_urls)

        response = await api.post(f"{TENANT}/whatsapp-number", headers=second, json=_body(phone))

        assert response.status_code == 409
        assert response.json()["error"] == "phone_already_connected"
        assert len(stub.created_urls) == created_before

    async def test_if_yclouds_webhook_has_no_secret_nothing_is_connected(
        self, api: AsyncClient, app: FastAPI, mail: Any, db: Session
    ) -> None:
        headers, _ = await _owner(api, mail, db)
        phone = _unique_number()
        _use(app, YCloudStub(phone, secret=None))

        response = await api.post(f"{TENANT}/whatsapp-number", headers=headers, json=_body(phone))

        assert response.status_code == 400
        assert response.json()["error"] == "webhook_not_created"
        assert (await api.get(f"{TENANT}/whatsapp-number", headers=headers)).status_code == 404
