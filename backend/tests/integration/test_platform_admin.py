"""The platform admin surface and its audit trail (TASK-018).

Two things are being proved here, and the second matters more than the first.

1. The queue works: a request moves requested -> in_progress -> connected or
   rejected, and connecting stores encrypted credentials.
2. **Nothing privileged happens without a row in `audit_logs`.** This is the
   only surface that crosses tenant boundaries, and the tenant cannot see what
   was done to it. The audit log is the only place those actions are visible
   at all (RF-012), so a test that checked the state change and not the record
   would be checking the easy half.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User
from app.models.whatsapp import ConnectionRequest, WhatsAppNumber
from app.services.connection import read_credentials

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
ADMIN = "/api/v1/platform-admin"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')

GOOD_CREDENTIALS = {
    "provider": "ycloud",
    "values": {
        "waba_id": "waba-1234567890",
        "phone_number_id": "phone-1234567890",
        "api_key": "yc-live-abcdef123456",
    },
}


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


def _unique_number() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _register(
    api: AsyncClient, mail: Any, db: Session, *, phone_verified: bool = True
) -> tuple[dict[str, str], uuid.UUID]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": "Panaderia Quispe",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})

    user_id = uuid.UUID(created.json()["user_id"])
    if phone_verified:
        user = db.get(User, user_id)
        assert user is not None
        user.phone_verified_at = datetime.now(UTC)
        db.commit()

    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    return {"Authorization": f"Bearer {session.json()['access_token']}"}, user_id


async def _admin(api: AsyncClient, mail: Any, db: Session) -> tuple[dict[str, str], uuid.UUID]:
    """A platform operator. The flag is set directly: there is deliberately no
    endpoint that grants it, because an API that can make somebody a platform
    admin is an API that can be used to make somebody a platform admin."""
    headers, user_id = await _register(api, mail, db)
    user = db.get(User, user_id)
    assert user is not None
    user.is_platform_admin = True
    db.commit()
    # Re-login: the flag is read from the database on every request, so the
    # existing token would work too - proving that is its own test below.
    return headers, user_id


async def _file_request(api: AsyncClient, headers: dict[str, str]) -> tuple[uuid.UUID, str]:
    phone = _unique_number()
    created = await api.post(
        f"{TENANT}/connection-requests",
        headers=headers,
        json={
            "phone_e164": phone,
            "display_name": "Panaderia Leo",
            "business_description": "Panaderia artesanal en Miraflores",
        },
    )
    assert created.status_code == 201, created.text
    return uuid.UUID(created.json()["id"]), phone


def _audit(db: Session, action: str, tenant_id: uuid.UUID) -> list[AuditLog]:
    """Audit entries for one action, against one tenant.

    Scoped, and it has to be. Counting the whole table asserted "this is the
    only such action anywhere in the database", which is true only of a
    database nobody has touched - the end-to-end suite connects real numbers
    and writes real audit rows, and these tests then failed for a reason that
    had nothing to do with auditing. Third time this shape of bug has
    appeared, after products and knowledge sources.
    """
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.tenant_id == tenant_id)
        )
        .scalars()
        .all()
    )


class TestTheGate:
    async def test_an_ordinary_user_cannot_see_the_queue(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """404, not 403. Whether this surface exists is not something an
        ordinary account should be able to confirm."""
        headers, _ = await _register(api, mail, db)
        response = await api.get(f"{ADMIN}/connection-requests", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/connection-requests"),
            ("get", "/tenants"),
            ("get", f"/tenants/{uuid.uuid4()}"),
            ("post", f"/tenants/{uuid.uuid4()}/extend-trial"),
        ],
    )
    async def test_every_endpoint_is_gated(
        self, api: AsyncClient, mail: Any, db: Session, method: str, path: str
    ) -> None:
        """Every one, not just the first. A gate applied per-endpoint is a gate
        somebody forgets on the next endpoint."""
        headers, _ = await _register(api, mail, db)
        response = await api.request(method.upper(), f"{ADMIN}{path}", headers=headers, json={})
        assert response.status_code == 404

    async def test_no_token_is_401(self, api: AsyncClient) -> None:
        assert (await api.get(f"{ADMIN}/connection-requests")).status_code == 401

    async def test_revoking_the_flag_takes_effect_immediately(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The flag is read from the database, never from the token. Revoking
        it must take effect now, not within the access token's fifteen
        minutes - the same rule roles follow."""
        headers, user_id = await _admin(api, mail, db)
        assert (await api.get(f"{ADMIN}/tenants", headers=headers)).status_code == 200

        user = db.get(User, user_id)
        assert user is not None
        user.is_platform_admin = False
        db.commit()

        # Same token, no re-login.
        assert (await api.get(f"{ADMIN}/tenants", headers=headers)).status_code == 404


class TestTheQueue:
    async def test_the_queue_shows_open_requests_oldest_first(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        first, _ = await _file_request(api, tenant_headers)
        second, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        # Stamped explicitly, because the fixture runs the whole test inside
        # one transaction and `created_at` defaults to `now()` - which in
        # PostgreSQL is the *transaction* start time, identical for both rows.
        # The ordering is then a tie and the queue may return either first,
        # which made this case flaky rather than wrong. Production is
        # unaffected: every request is its own transaction.
        base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        db.get(ConnectionRequest, first).created_at = base  # type: ignore[union-attr]
        db.get(ConnectionRequest, second).created_at = base + timedelta(minutes=5)  # type: ignore[union-attr]
        db.commit()

        body = (
            await api.get(
                f"{ADMIN}/connection-requests?status=requested&limit=200",
                headers=admin_headers,
            )
        ).json()

        # The queue is platform-wide and paged, so it holds whatever else the
        # database contains. What this case is about is the *ordering*, so it
        # is asserted among this test's own two rows - a raw `.index()` into
        # the full list breaks the moment anything else is queued, which the
        # end-to-end suite guarantees.
        mine = [row["id"] for row in body if row["id"] in {str(first), str(second)}]
        assert mine == [str(first), str(second)], "oldest first"

    async def test_closed_requests_never_push_an_open_one_off_the_page(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Without a filter the open ones come first: with more closed requests
        than fit in a page, a new request was not listed (found by e2e, 018)."""
        tenant_headers, _ = await _register(api, mail, db)
        closed, _ = await _file_request(api, tenant_headers)
        waiting, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)
        row = db.get(ConnectionRequest, closed)
        assert row is not None
        row.status = "rejected"
        row.rejection_reason = "Número de prueba"
        row.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        db.commit()

        body = (await api.get(f"{ADMIN}/connection-requests", headers=admin_headers)).json()
        statuses = [r["status"] for r in body]
        first_closed = next(
            (i for i, s in enumerate(statuses) if s not in ("requested", "in_progress")),
            len(statuses),
        )
        assert all(s in ("requested", "in_progress") for s in statuses[:first_closed])
        assert all(s not in ("requested", "in_progress") for s in statuses[first_closed:])
        ids = [r["id"] for r in body]
        assert str(waiting) in ids[:first_closed]

    async def test_the_queue_crosses_tenants(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The deliberate exception to tenant scoping. An admin queue that
        showed only their own tenant would show nothing at all."""
        headers_a, _ = await _register(api, mail, db)
        headers_b, _ = await _register(api, mail, db)
        request_a, _ = await _file_request(api, headers_a)
        request_b, _ = await _file_request(api, headers_b)
        admin_headers, _ = await _admin(api, mail, db)

        ids = {
            row["id"]
            for row in (await api.get(f"{ADMIN}/connection-requests", headers=admin_headers)).json()
        }
        assert {str(request_a), str(request_b)} <= ids


class TestTheHappyPath:
    async def test_requested_to_in_progress_to_connected(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The flow the task asks for, end to end."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, phone = await _file_request(api, tenant_headers)
        admin_headers, admin_id = await _admin(api, mail, db)

        taken = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "in_progress", "admin_notes": "Solicitando WABA en YCloud"},
        )
        assert taken.status_code == 200
        assert taken.json()["status"] == "in_progress"
        assert taken.json()["processed_by"] == str(admin_id)

        connected = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )
        assert connected.status_code == 200, connected.text
        assert connected.json()["status"] == "connected"

        # The number is live for the tenant that asked for it.
        number = db.execute(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_e164 == phone)
        ).scalar_one()
        assert number.status == "connected"
        assert number.provider == "ycloud"

        # And the tenant can see it, without credentials.
        theirs = (await api.get(f"{TENANT}/whatsapp-number", headers=tenant_headers)).json()
        assert theirs["phone_e164"] == phone
        assert "credentials_enc" not in theirs

    async def test_the_credentials_are_encrypted_at_rest_and_decrypt_back(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """RF-009. A database dump must not be a set of working WhatsApp
        credentials for every business on the platform."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, phone = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )

        number = db.execute(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_e164 == phone)
        ).scalar_one()
        assert number.credentials_enc is not None
        # The plaintext key must not be findable in the stored bytes.
        assert b"yc-live-abcdef123456" not in number.credentials_enc

        recovered = read_credentials(number)
        assert recovered is not None
        assert recovered.values["api_key"] == "yc-live-abcdef123456"

    async def test_rejection_records_a_reason_the_customer_can_read(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        rejected = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={
                "status": "rejected",
                "rejection_reason": "El nombre no cumple las politicas de Meta",
            },
        )
        assert rejected.status_code == 200

        history = (await api.get(f"{TENANT}/connection-requests", headers=tenant_headers)).json()
        assert history[0]["rejection_reason"] == "El nombre no cumple las politicas de Meta"


class TestTheRefusals:
    async def test_connecting_without_credentials_is_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Connecting with nothing would produce a number that cannot send,
        and the failure would surface at the first customer message."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        response = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "credentials_required"

    async def test_placeholder_credentials_are_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The realistic failure is somebody tabbing through the form and
        saving 'TODO', not a malformed token."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        response = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={
                "status": "connected",
                "credentials": {
                    "provider": "ycloud",
                    "values": {"waba_id": "TODO", "phone_number_id": "x", "api_key": "y"},
                },
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "credentials_required"

    async def test_a_rejection_without_a_reason_is_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """A rejection the customer cannot act on just becomes a support
        ticket asking what to change."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        response = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "rejected"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "rejection_reason_required"

    async def test_a_connected_request_cannot_be_reopened(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The number is live. A second connection would be two businesses on
        one line - which is a customer talking to the wrong company."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )
        again = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "in_progress"},
        )
        assert again.status_code == 400
        assert again.json()["error"] == "illegal_transition"

    async def test_connecting_a_number_another_tenant_holds_is_a_conflict(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """RF-008 as a race: the number was free when the admin opened the
        queue and taken by the time they pressed connect. The unique index
        decides, not the check in front of it."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, phone = await _file_request(api, tenant_headers)
        other_headers, _ = await _register(api, mail, db)
        other_tenant = uuid.UUID((await api.get(TENANT, headers=other_headers)).json()["id"])
        admin_headers, _ = await _admin(api, mail, db)

        db.add(
            WhatsAppNumber(
                tenant_id=other_tenant,
                phone_e164=phone,
                display_name="Se adelanto",
                status="connected",
            )
        )
        db.commit()

        response = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "phone_already_connected"

        # And the request did not silently move.
        row = db.get(ConnectionRequest, request_id)
        assert row is not None
        db.refresh(row)
        assert row.status == "requested"

    async def test_an_unknown_request_is_404(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        admin_headers, _ = await _admin(api, mail, db)
        response = await api.put(
            f"{ADMIN}/connection-requests/{uuid.uuid4()}",
            headers=admin_headers,
            json={"status": "in_progress"},
        )
        assert response.status_code == 404


class TestTheAuditTrail:
    """RF-012. The tenant cannot see what was done to it; this is the only
    place these actions are visible at all."""

    async def test_connecting_is_audited_without_the_credentials(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        request_id, phone = await _file_request(api, tenant_headers)
        admin_headers, admin_id = await _admin(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )

        entries = _audit(db, "connection_request.connected", tenant_id)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor_id == admin_id
        assert entry.tenant_id == tenant_id
        assert entry.detail["phone_e164"] == phone

        # The whole point of encrypting them is defeated by logging them in
        # clear, and the audit log is read far more widely than the table.
        serialised = str(entry.detail)
        assert "yc-live-abcdef123456" not in serialised
        assert "api_key" not in serialised

    async def test_rejecting_is_audited_with_the_reason(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "rejected", "rejection_reason": "WABA ya activa en otra cuenta"},
        )

        entries = _audit(db, "connection_request.rejected", tenant_id)
        assert len(entries) == 1
        assert entries[0].detail["rejection_reason"] == "WABA ya activa en otra cuenta"

    async def test_a_refused_action_leaves_no_audit_entry(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """An audit log that records attempts as if they were actions is worse
        than none: it makes a rejected connection look like a connected one."""
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        refused = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected"},
        )
        assert refused.status_code == 400
        assert _audit(db, "connection_request.connected", tenant_id) == []


class TestTheTenantIsTold:
    """The customer filed a request and then waited on a human being. Without
    a message the only way to learn the work is done is to keep opening the
    panel - which is the anxiety the request status was meant to remove."""

    async def test_connecting_emails_the_owner(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        request_id, phone = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)
        before = len(mail.sent)

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )

        assert len(mail.sent) == before + 1
        sent = mail.last
        assert phone in sent.text
        assert "conectamos" in sent.text.lower()

    async def test_rejecting_emails_the_reason(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The reason is the whole message: it is the only thing that lets
        them correct the details and file again."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "rejected", "rejection_reason": "El perfil no tiene foto valida"},
        )
        assert "El perfil no tiene foto valida" in mail.last.text

    async def test_taking_a_request_does_not_email_anyone(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """`in_progress` is an internal step. Mailing the customer every time
        somebody opens their ticket trains them to ignore the messages."""
        tenant_headers, _ = await _register(api, mail, db)
        request_id, _ = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)
        before = len(mail.sent)

        await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "in_progress"},
        )
        assert len(mail.sent) == before

    async def test_a_mail_outage_does_not_undo_the_connection(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The number is live either way.

        And the admin is told it succeeded, because it did. A 500 here would
        say the action failed when it did not, and the natural response -
        retrying - hits `illegal_transition` and looks like a second bug.
        """
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        request_id, phone = await _file_request(api, tenant_headers)
        admin_headers, _ = await _admin(api, mail, db)

        async def explode(_message: Any) -> None:
            raise RuntimeError("smtp is down")

        mail.send = explode  # type: ignore[method-assign]

        response = await api.put(
            f"{ADMIN}/connection-requests/{request_id}",
            headers=admin_headers,
            json={"status": "connected", "credentials": GOOD_CREDENTIALS},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "connected"

        number = db.execute(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_e164 == phone)
        ).scalar_one()
        assert number.status == "connected"
        assert len(_audit(db, "connection_request.connected", tenant_id)) == 1


class TestTenants:
    async def test_the_list_crosses_tenants_and_can_be_searched(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        await _register(api, mail, db)
        admin_headers, _ = await _admin(api, mail, db)

        everything = (await api.get(f"{ADMIN}/tenants", headers=admin_headers)).json()
        assert len(everything) >= 2

        filtered = (
            await api.get(f"{ADMIN}/tenants?search=Panaderia", headers=admin_headers)
        ).json()
        assert all("panaderia" in row["name"].lower() for row in filtered)

    async def test_the_detail_carries_the_figures_an_operator_needs(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        admin_headers, _ = await _admin(api, mail, db)

        body = (await api.get(f"{ADMIN}/tenants/{tenant_id}", headers=admin_headers)).json()
        assert body["id"] == str(tenant_id)
        assert body["owner_email"] is not None
        assert body["members"] == 1
        assert body["products"] == 0
        assert body["knowledge_chars"] == 0
        assert body["has_number"] is False

    async def test_an_unknown_tenant_is_404(self, api: AsyncClient, mail: Any, db: Session) -> None:
        admin_headers, _ = await _admin(api, mail, db)
        response = await api.get(f"{ADMIN}/tenants/{uuid.uuid4()}", headers=admin_headers)
        assert response.status_code == 404


class TestExtendTrial:
    async def test_it_extends_and_audits(self, api: AsyncClient, mail: Any, db: Session) -> None:
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        admin_headers, admin_id = await _admin(api, mail, db)

        before = (await api.get(f"{ADMIN}/tenants/{tenant_id}", headers=admin_headers)).json()
        original = datetime.fromisoformat(before["trial_ends_at"])

        response = await api.post(
            f"{ADMIN}/tenants/{tenant_id}/extend-trial",
            headers=admin_headers,
            json={"days": 7, "reason": "Demora nuestra en conectar el numero"},
        )
        assert response.status_code == 200
        assert datetime.fromisoformat(response.json()["trial_ends_at"]) == original + timedelta(
            days=7
        )

        entries = _audit(db, "trial.extended", tenant_id)
        assert len(entries) == 1
        assert entries[0].actor_id == admin_id
        assert entries[0].tenant_id == tenant_id
        assert entries[0].detail["days"] == 7
        assert entries[0].detail["reason"] == "Demora nuestra en conectar el numero"

    async def test_a_lapsed_trial_is_extended_from_now_not_from_the_old_date(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Otherwise 'seven more days' on a trial that expired last week grants
        nothing, and the customer is compensated for our delay with an expiry
        in the past."""
        from app.models.tenant import Tenant

        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        admin_headers, _ = await _admin(api, mail, db)

        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.trial_ends_at = datetime.now(UTC) - timedelta(days=10)
        db.commit()

        response = await api.post(
            f"{ADMIN}/tenants/{tenant_id}/extend-trial",
            headers=admin_headers,
            json={"days": 7, "reason": "Reactivacion"},
        )
        assert response.status_code == 200
        assert datetime.fromisoformat(response.json()["trial_ends_at"]) > datetime.now(UTC)

    async def test_extending_a_paused_tenant_un_pauses_it(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """An extension that leaves the account paused is a date change with no
        effect the customer can see."""
        from app.models.tenant import Tenant

        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        admin_headers, _ = await _admin(api, mail, db)

        tenant = db.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.status = "paused"
        tenant.paused_at = datetime.now(UTC)
        tenant.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()

        body = (
            await api.post(
                f"{ADMIN}/tenants/{tenant_id}/extend-trial",
                headers=admin_headers,
                json={"days": 7, "reason": "Cliente pidio mas tiempo"},
            )
        ).json()
        assert body["status"] == "trial"
        assert body["paused_at"] is None

    @pytest.mark.parametrize("days", [0, -5, 3650])
    async def test_an_implausible_extension_is_refused(
        self, api: AsyncClient, mail: Any, db: Session, days: int
    ) -> None:
        """A typo of 3650 should be a 400, not a decade of free service."""
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        admin_headers, _ = await _admin(api, mail, db)

        response = await api.post(
            f"{ADMIN}/tenants/{tenant_id}/extend-trial",
            headers=admin_headers,
            json={"days": days, "reason": "Motivo valido"},
        )
        assert response.status_code == 400

    async def test_an_extension_without_a_reason_is_refused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """A trial extended without one is indistinguishable from an account
        quietly kept free."""
        tenant_headers, _ = await _register(api, mail, db)
        tenant_id = uuid.UUID((await api.get(TENANT, headers=tenant_headers)).json()["id"])
        admin_headers, _ = await _admin(api, mail, db)

        response = await api.post(
            f"{ADMIN}/tenants/{tenant_id}/extend-trial",
            headers=admin_headers,
            json={"days": 7},
        )
        assert response.status_code == 400
