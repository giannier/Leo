"""The platform admin surface is the one deliberate exception (TASK-018).

Everything else in this application is confined to one business by
construction. `/platform-admin` is not, and cannot be: connecting a number and
extending a trial are things done *to* a tenant by somebody outside it.

An exception to the rule that keeps every other query honest has to be bounded
on all sides, so this file proves the boundary rather than the exception:

* Being the **owner** of a business - the highest tenant role there is - grants
  none of it. Owner and platform admin are different words for different
  things, and conflating them is the realistic mistake.
* The flag cannot be **granted** through any tenant-facing payload. A
  privilege-escalation route into this surface would make every other
  isolation test in this directory meaningless.
* An admin acting on tenant A leaves tenant B untouched.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
ADMIN = "/api/v1/platform-admin"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


def _unique_number() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _owner(
    api: AsyncClient, mail: Any, db: Session, label: str, **extra: Any
) -> tuple[dict[str, str], uuid.UUID]:
    body: dict[str, Any] = {
        "full_name": f"Owner {label}",
        "email": f"{label}-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": f"Negocio {label}",
    }
    body.update(extra)
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})

    user_id = uuid.UUID(created.json()["user_id"])
    user = db.get(User, user_id)
    assert user is not None
    user.phone_verified_at = datetime.now(UTC)
    db.commit()

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}, user_id


ADMIN_ENDPOINTS = [
    ("GET", "/connection-requests"),
    ("PUT", f"/connection-requests/{uuid.uuid4()}"),
    ("GET", "/tenants"),
    ("GET", f"/tenants/{uuid.uuid4()}"),
    ("POST", f"/tenants/{uuid.uuid4()}/extend-trial"),
]


class TestOwnerIsNotPlatformAdmin:
    @pytest.mark.parametrize(("method", "path"), ADMIN_ENDPOINTS)
    async def test_the_owner_of_a_business_reaches_nothing(
        self, api: AsyncClient, mail: Any, db: Session, method: str, path: str
    ) -> None:
        """Owner is the highest role inside a business. It stops at the edge
        of that business, and this surface is outside it."""
        headers, _ = await _owner(api, mail, db, "owner")
        response = await api.request(method, f"{ADMIN}{path}", headers=headers, json={})
        assert response.status_code == 404, f"{method} {path} answered {response.status_code}"


class TestTheFlagCannotBeGranted:
    async def test_registration_cannot_ask_for_it(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The realistic escalation attempt: pass the column name and hope the
        payload is forwarded to the model. If this ever succeeds, every other
        test in this directory stops meaning anything."""
        headers, user_id = await _owner(api, mail, db, "sneaky", is_platform_admin=True)
        user = db.get(User, user_id)
        assert user is not None
        db.refresh(user)
        assert user.is_platform_admin is False
        assert (await api.get(f"{ADMIN}/tenants", headers=headers)).status_code == 404

    async def test_no_route_in_the_application_can_set_it(self) -> None:
        """A static check, deliberately: it holds for routes nobody thought to
        test, including ones added later. The flag is granted by a human with
        database access, and that is the whole design."""
        from pydantic import BaseModel

        from app.main import create_app

        offenders: list[str] = []
        for route in create_app().routes:
            body_field = getattr(route, "body_field", None)
            if body_field is None:
                continue
            annotation = body_field.type_
            accepts_model = isinstance(annotation, type) and issubclass(annotation, BaseModel)
            if accepts_model and "is_platform_admin" in annotation.model_fields:
                offenders.append(f"{getattr(route, 'path', route)} -> {annotation.__name__}")

        assert offenders == [], f"these payloads accept the platform admin flag: {offenders}"


class TestAdminActionsDoNotSpill:
    async def test_extending_one_trial_leaves_the_other_alone(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The admin surface crosses tenants by design; that is not licence to
        touch more than one at a time."""
        headers_a, _ = await _owner(api, mail, db, "alpha")
        headers_b, _ = await _owner(api, mail, db, "beta")
        admin_headers, admin_id = await _owner(api, mail, db, "operator")

        admin_user = db.get(User, admin_id)
        assert admin_user is not None
        admin_user.is_platform_admin = True
        db.commit()

        tenant_a = uuid.UUID((await api.get(TENANT, headers=headers_a)).json()["id"])
        tenant_b = uuid.UUID((await api.get(TENANT, headers=headers_b)).json()["id"])
        before_b = (await api.get(f"{ADMIN}/tenants/{tenant_b}", headers=admin_headers)).json()

        extended = await api.post(
            f"{ADMIN}/tenants/{tenant_a}/extend-trial",
            headers=admin_headers,
            json={"days": 14, "reason": "Piloto extendido"},
        )
        assert extended.status_code == 200

        after_b = (await api.get(f"{ADMIN}/tenants/{tenant_b}", headers=admin_headers)).json()
        assert after_b["trial_ends_at"] == before_b["trial_ends_at"]

        # And the audit entry names exactly one tenant - the one acted on.
        entries = list(
            db.execute(select(AuditLog).where(AuditLog.action == "trial.extended")).scalars().all()
        )
        assert [entry.tenant_id for entry in entries] == [tenant_a]

    async def test_a_tenant_cannot_read_the_audit_log_about_itself(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """There is no tenant-facing route to `audit_logs`, and there should
        not be: it records what the platform did, including internal notes,
        and is not the customer's activity feed."""
        headers, _ = await _owner(api, mail, db, "curious")
        for path in ("/audit-logs", "/audit", "/platform-admin/audit-logs"):
            response = await api.get(f"/api/v1{path}", headers=headers)
            assert response.status_code == 404
