"""The lifecycle jobs run platform-wide but must act on one tenant (TASK-019).

These jobs are a second place where code legitimately reads across tenants -
a scheduler has to look at every business to find the expiring ones. That
makes them the same kind of risk as the admin surface: the query that finds
candidates is global, and every query *inside* the loop has to be scoped, or
pausing one business closes another's conversations.

The nastiest version is the farewell. `pause_tenant` writes a message into
every open thread it finds; if that select loses its tenant predicate, a
customer of business B receives business A's goodbye, on B's number, while B
is still paying and still trading.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.conversation import Conversation, Message
from app.models.plan import Plan
from app.models.tenant import Tenant, TenantNotification
from app.services.trial import record_ai_message
from app.workers.trial_lifecycle import run_tenant_archiver, run_trial_enforcer

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')

NOW = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


def _unique_number() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _tenant(api: AsyncClient, mail: Any, db: Session, label: str) -> Tenant:
    body = {
        "full_name": f"Owner {label}",
        "email": f"{label}-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": f"Negocio {label}",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

    tenant = db.get(Tenant, uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"]))
    assert tenant is not None
    return tenant


class TestTheFarewellStaysHome:
    async def test_pausing_one_business_does_not_close_anothers_conversations(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        expiring = await _tenant(api, mail, db, "expiring")
        healthy = await _tenant(api, mail, db, "healthy")

        db.add(
            AgentConfig(tenant_id=expiring.id, is_active=True, farewell_on_pause="Hasta pronto.")
        )
        theirs = Conversation(tenant_id=expiring.id, channel="whatsapp", status="open")
        ours = Conversation(tenant_id=healthy.id, channel="whatsapp", status="open")
        db.add_all([theirs, ours])
        expiring.trial_ends_at = NOW - timedelta(hours=1)
        healthy.trial_ends_at = NOW + timedelta(days=9)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)

        db.refresh(theirs)
        db.refresh(ours)
        assert theirs.status == "closed"
        assert ours.status == "open", "another business's thread was closed"

    async def test_the_goodbye_is_never_written_into_a_foreign_thread(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """A customer of business B receiving business A's goodbye, on B's
        number, while B is still trading."""
        expiring = await _tenant(api, mail, db, "expiring")
        healthy = await _tenant(api, mail, db, "healthy")

        db.add(
            AgentConfig(
                tenant_id=expiring.id,
                is_active=True,
                farewell_on_pause="Cerramos por hoy, gracias.",
            )
        )
        ours = Conversation(tenant_id=healthy.id, channel="whatsapp", status="open")
        db.add(ours)
        expiring.trial_ends_at = NOW - timedelta(hours=1)
        healthy.trial_ends_at = NOW + timedelta(days=9)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)

        leaked = (
            db.execute(select(Message).where(Message.conversation_id == ours.id)).scalars().all()
        )
        assert leaked == []

    async def test_the_healthy_tenant_is_not_paused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        expiring = await _tenant(api, mail, db, "expiring")
        healthy = await _tenant(api, mail, db, "healthy")
        expiring.trial_ends_at = NOW - timedelta(hours=1)
        healthy.trial_ends_at = NOW + timedelta(days=9)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)

        db.refresh(expiring)
        db.refresh(healthy)
        assert expiring.status == "paused"
        assert healthy.status == "trial"


class TestTheCounterIsPerTenant:
    async def test_metering_one_business_does_not_move_anothers_counter(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Shared quota would be the worst kind of bug here: it bills one
        customer for another's traffic, and it ends their trial early."""
        busy = await _tenant(api, mail, db, "busy")
        quiet = await _tenant(api, mail, db, "quiet")
        before = quiet.ai_messages_used

        for _ in range(3):
            record_ai_message(db, busy.id, channel="whatsapp")
        db.commit()

        db.refresh(busy)
        db.refresh(quiet)
        assert busy.ai_messages_used == 3
        assert quiet.ai_messages_used == before

    async def test_one_business_over_quota_does_not_pause_another(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        busy = await _tenant(api, mail, db, "busy")
        quiet = await _tenant(api, mail, db, "quiet")
        plan = db.get(Plan, busy.plan_id)
        assert plan is not None

        busy.ai_messages_used = plan.max_ai_messages
        busy.trial_ends_at = NOW + timedelta(days=9)
        quiet.trial_ends_at = NOW + timedelta(days=9)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)

        db.refresh(busy)
        db.refresh(quiet)
        assert busy.status == "paused"
        assert quiet.status == "trial"


class TestNotificationsAreScoped:
    async def test_a_claim_by_one_tenant_does_not_silence_another(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The idempotency key is (tenant_id, kind). Keyed on `kind` alone it
        would send the day -3 warning to the first customer who needed it and
        to nobody else, ever."""
        first = await _tenant(api, mail, db, "first")
        second = await _tenant(api, mail, db, "second")
        first.trial_ends_at = NOW - timedelta(hours=1)
        second.trial_ends_at = NOW - timedelta(hours=1)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)

        kinds = {
            (row.tenant_id, row.kind)
            for row in db.execute(
                select(TenantNotification).where(
                    TenantNotification.tenant_id.in_([first.id, second.id])
                )
            )
            .scalars()
            .all()
        }
        assert kinds == {(first.id, "trial_ended"), (second.id, "trial_ended")}


class TestArchivingIsScoped:
    async def test_archiving_one_tenant_leaves_the_other_paused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        old = await _tenant(api, mail, db, "old")
        recent = await _tenant(api, mail, db, "recent")

        old.status = "paused"
        old.paused_at = NOW - timedelta(days=40)
        recent.status = "paused"
        recent.paused_at = NOW - timedelta(days=2)
        db.commit()

        await run_tenant_archiver(db, now=NOW)

        db.refresh(old)
        db.refresh(recent)
        assert old.status == "archived"
        assert recent.status == "paused"
