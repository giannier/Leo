"""The trial lifecycle, driven by a simulated clock (TASK-019).

`tasks.md` asks for freezegun. The clock is **injected** instead: every
function that needs the time of day takes `now` as an argument, so a test
drives the transition table by passing dates rather than by patching
`datetime` process-wide. That is a simulated clock in the sense the task
means, it needs no new dependency, and it is stricter - a code path that
secretly reads the wall clock fails here, where under freezegun it would
silently pass because the global clock was frozen too.

What is being proved: every transition in Clarification 004, both ways a
trial can end (Clarification 001), and - the part that actually bites in
production - that none of these jobs sends the same email twice.
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

from app.models.business import AgentConfig
from app.models.conversation import Conversation, Message
from app.models.plan import Plan
from app.models.tenant import Tenant, TenantNotification
from app.services.trial import (
    DEFAULT_FAREWELL,
    RETENTION_DAYS,
    WIZARD_IDLE_HOURS,
    agent_may_reply,
    due_notice,
    record_ai_message,
)
from app.workers.trial_lifecycle import (
    run_tenant_archiver,
    run_trial_enforcer,
    run_trial_notifier,
    run_wizard_reminder,
)

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"
PASSWORD = "una-clave-suficientemente-larga"
_HREF = re.compile(r'href="([^"]+)"')

# A fixed instant, so every expectation in this file is arithmetic rather than
# a race with the wall clock.
NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _link_token(message: Any) -> str:
    for href in _HREF.findall(message.html):
        query = parse_qs(urlparse(href).query)
        if "token" in query:
            return str(query["token"][0])
    raise AssertionError("no tokenised link in the email body")


def _unique_number() -> str:
    return f"+519{uuid.uuid4().int % 10**8:08d}"


async def _tenant(api: AsyncClient, mail: Any, db: Session) -> tuple[dict[str, str], Tenant]:
    body = {
        "full_name": "Ana Quispe",
        "email": f"ana-{uuid.uuid4().hex[:12]}@example.com",
        "password": PASSWORD,
        "phone_e164": _unique_number(),
        "business_name": "Panaderia Quispe",
    }
    created = await api.post(f"{AUTH}/register", json=body)
    assert created.status_code == 201, created.text
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    session = await api.post(f"{AUTH}/login", json={"email": body["email"], "password": PASSWORD})
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

    tenant_id = uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"])
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    return headers, tenant


def _notifications(db: Session, tenant_id: uuid.UUID) -> set[str]:
    return set(
        db.execute(select(TenantNotification.kind).where(TenantNotification.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def _quota(db: Session, tenant: Tenant) -> int:
    plan = db.get(Plan, tenant.plan_id)
    assert plan is not None
    return plan.max_ai_messages


class TestMetering:
    """RF-004: only the real channel counts."""

    async def test_a_whatsapp_reply_is_counted(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        before = tenant.ai_messages_used

        result = record_ai_message(db, tenant.id, channel="whatsapp")
        db.commit()

        assert result.counted is True
        assert result.used == before + 1
        db.refresh(tenant)
        assert tenant.ai_messages_used == before + 1

    async def test_a_sandbox_reply_is_not(self, api: AsyncClient, mail: Any, db: Session) -> None:
        """Clarification 002. The sandbox is where the customer decides the
        product is worth paying for; charging for it discourages exactly the
        experimentation that converts."""
        _, tenant = await _tenant(api, mail, db)
        before = tenant.ai_messages_used

        result = record_ai_message(db, tenant.id, channel="sandbox")
        db.commit()

        assert result.counted is False
        db.refresh(tenant)
        assert tenant.ai_messages_used == before

    async def test_the_allowance_reads_as_spent_at_the_limit_not_past_it(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        limit = _quota(db, tenant)
        tenant.ai_messages_used = limit - 1
        db.commit()

        result = record_ai_message(db, tenant.id, channel="whatsapp")
        db.commit()
        assert result.used == limit
        assert result.exhausted is True


class TestWhoMayReply:
    """The seam Feature 002 calls. Without it, `paused` is a column and
    nothing more."""

    async def test_an_active_trial_with_the_agent_on_may_reply(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
        db.commit()
        assert agent_may_reply(db, tenant) is True

    async def test_a_paused_tenant_may_not(self, api: AsyncClient, mail: Any, db: Session) -> None:
        _, tenant = await _tenant(api, mail, db)
        db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
        tenant.status = "paused"
        db.commit()
        assert agent_may_reply(db, tenant) is False

    async def test_a_trial_over_quota_may_not_even_before_the_enforcer_runs(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The enforcer is hourly, and an hour at the end of a trial is enough
        conversation to hand out a few dozen free replies."""
        _, tenant = await _tenant(api, mail, db)
        db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
        tenant.ai_messages_used = _quota(db, tenant)
        db.commit()
        assert tenant.status == "trial"
        assert agent_may_reply(db, tenant) is False

    async def test_an_agent_switched_off_may_not(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        db.add(AgentConfig(tenant_id=tenant.id, is_active=False))
        db.commit()
        assert agent_may_reply(db, tenant) is False


class TestWhichNoticeIsDue:
    @pytest.mark.parametrize(
        ("days_left", "expected"),
        [
            (14, None),
            (4, None),
            (3, "trial_ending_3d"),
            (2, "trial_ending_3d"),
            (1, "trial_ending_1d"),
            (0, "trial_ended"),
            (-2, "trial_ended"),
        ],
    )
    async def test_the_whole_table(
        self, api: AsyncClient, mail: Any, db: Session, days_left: int, expected: str | None
    ) -> None:
        """Thresholds, not exact dates. A daily job that misses a tick - a
        deploy, a restart - would skip the notice entirely on an equality
        test, and the customer would find out their trial ended by noticing
        the agent stopped answering."""
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW + timedelta(days=days_left)
        db.commit()
        assert due_notice(db, tenant, now=NOW) == expected

    async def test_an_exhausted_quota_reads_as_ended_whatever_the_date_says(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Whichever happens first ends the trial (Clarification 001)."""
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW + timedelta(days=10)
        tenant.ai_messages_used = _quota(db, tenant)
        db.commit()
        assert due_notice(db, tenant, now=NOW) == "trial_ended"


class TestTheNotifier:
    async def test_it_sends_the_three_day_warning_once(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW + timedelta(days=3)
        db.commit()

        await run_trial_notifier(db, email_provider=mail, now=NOW)
        assert "3 dias" in mail.last.text
        assert _notifications(db, tenant.id) == {"trial_ending_3d"}

        # The next day the same notice is still "due" by threshold. The
        # primary key is what stops a second email, not a date comparison.
        await run_trial_notifier(db, email_provider=mail, now=NOW + timedelta(days=1))
        assert _notifications(db, tenant.id) == {"trial_ending_3d"}

    async def test_it_walks_the_customer_through_all_three(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        ends = NOW + timedelta(days=3)
        tenant.trial_ends_at = ends
        db.commit()

        await run_trial_notifier(db, email_provider=mail, now=ends - timedelta(days=3))
        await run_trial_notifier(db, email_provider=mail, now=ends - timedelta(days=1))
        await run_trial_notifier(db, email_provider=mail, now=ends)

        assert _notifications(db, tenant.id) == {
            "trial_ending_3d",
            "trial_ending_1d",
            "trial_ended",
        }

    async def test_a_missed_day_still_gets_the_more_urgent_notice(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The worker was down on day -3. The customer must still hear about
        it on day -1 rather than never."""
        _, tenant = await _tenant(api, mail, db)
        ends = NOW + timedelta(days=3)
        tenant.trial_ends_at = ends
        db.commit()

        await run_trial_notifier(db, email_provider=mail, now=ends - timedelta(hours=20))
        assert _notifications(db, tenant.id) == {"trial_ending_1d"}

    async def test_a_healthy_trial_is_left_alone(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW + timedelta(days=10)
        db.commit()
        await run_trial_notifier(db, email_provider=mail, now=NOW)
        assert _notifications(db, tenant.id) == set()


class TestTheEnforcer:
    async def test_an_expired_trial_is_paused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW - timedelta(hours=1)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        db.refresh(tenant)
        assert tenant.status == "paused"
        assert tenant.paused_at == NOW

    async def test_a_trial_out_of_messages_is_paused_before_its_date(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Whichever happens first (Clarification 001)."""
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW + timedelta(days=9)
        tenant.ai_messages_used = _quota(db, tenant)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        db.refresh(tenant)
        assert tenant.status == "paused"

    async def test_a_live_conversation_gets_a_farewell_and_is_closed(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Clarification 004. Leaving a customer mid-conversation with silence
        is the worst version of this: they keep typing to a business that has
        stopped answering and no longer says so."""
        _, tenant = await _tenant(api, mail, db)
        db.add(
            AgentConfig(
                tenant_id=tenant.id,
                is_active=True,
                farewell_on_pause="Gracias por escribir. Volvemos pronto.",
            )
        )
        conversation = Conversation(tenant_id=tenant.id, channel="whatsapp", status="open")
        db.add(conversation)
        tenant.trial_ends_at = NOW - timedelta(minutes=5)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)

        db.refresh(conversation)
        assert conversation.status == "closed"
        last = db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        ).scalar_one()
        assert last.content == "Gracias por escribir. Volvemos pronto."
        # `system`, not `agent`: the platform closed the thread, the model did
        # not produce this. A trace reader must not find it as agent output.
        assert last.author == "system"

    async def test_a_tenant_without_a_farewell_still_says_something(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        conversation = Conversation(tenant_id=tenant.id, channel="whatsapp", status="open")
        db.add(conversation)
        tenant.trial_ends_at = NOW - timedelta(minutes=5)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        last = db.execute(
            select(Message).where(Message.conversation_id == conversation.id)
        ).scalar_one()
        assert last.content == DEFAULT_FAREWELL

    async def test_a_sandbox_conversation_is_left_open(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Trying your own agent stays free after the trial ends - it is how a
        paused customer decides the product is worth paying for."""
        _, tenant = await _tenant(api, mail, db)
        conversation = Conversation(tenant_id=tenant.id, channel="sandbox", status="open")
        db.add(conversation)
        tenant.trial_ends_at = NOW - timedelta(minutes=5)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        db.refresh(conversation)
        assert conversation.status == "open"

    async def test_running_twice_does_not_pause_twice_or_send_two_farewells(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        conversation = Conversation(tenant_id=tenant.id, channel="whatsapp", status="open")
        db.add(conversation)
        tenant.trial_ends_at = NOW - timedelta(hours=1)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        await run_trial_enforcer(db, email_provider=mail, now=NOW + timedelta(hours=1))

        messages = (
            db.execute(select(Message).where(Message.conversation_id == conversation.id))
            .scalars()
            .all()
        )
        assert len(messages) == 1

    async def test_pausing_does_not_switch_the_agent_off(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """That switch belongs to the customer. Flipping it here would mean
        that paying to resume leaves the agent silent until somebody notices a
        toggle."""
        _, tenant = await _tenant(api, mail, db)
        config = AgentConfig(tenant_id=tenant.id, is_active=True)
        db.add(config)
        tenant.trial_ends_at = NOW - timedelta(hours=1)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        db.refresh(config)
        assert config.is_active is True

    async def test_the_day_zero_email_is_not_sent_twice_by_the_notifier(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """The pause and the day-0 notice are the same event. Without a shared
        claim the notifier would mail again about a trial that already ended."""
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW - timedelta(hours=1)
        db.commit()

        await run_trial_enforcer(db, email_provider=mail, now=NOW)
        assert _notifications(db, tenant.id) == {"trial_ended"}

        await run_trial_notifier(db, email_provider=mail, now=NOW)
        assert _notifications(db, tenant.id) == {"trial_ended"}


class TestTheArchiver:
    async def test_a_tenant_paused_past_the_window_is_archived(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        tenant.status = "paused"
        tenant.paused_at = NOW - timedelta(days=RETENTION_DAYS + 1)
        db.commit()

        await run_tenant_archiver(db, now=NOW)
        db.refresh(tenant)
        assert tenant.status == "archived"
        assert tenant.archived_at == NOW

    async def test_a_tenant_inside_the_window_is_not(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """30 days of retention is a promise, not a rounding target."""
        _, tenant = await _tenant(api, mail, db)
        tenant.status = "paused"
        tenant.paused_at = NOW - timedelta(days=RETENTION_DAYS - 1)
        db.commit()

        await run_tenant_archiver(db, now=NOW)
        db.refresh(tenant)
        assert tenant.status == "paused"

    async def test_archiving_deletes_nothing(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Soft, and reversible by an admin. Permanent deletion happens only
        at the account holder's request (Ley 29733)."""
        _, tenant = await _tenant(api, mail, db)
        conversation = Conversation(tenant_id=tenant.id, channel="whatsapp", status="closed")
        db.add(conversation)
        tenant.status = "paused"
        tenant.paused_at = NOW - timedelta(days=RETENTION_DAYS + 1)
        db.commit()

        await run_tenant_archiver(db, now=NOW)
        assert db.get(Conversation, conversation.id) is not None
        assert db.get(Tenant, tenant.id) is not None


class TestTheWizardReminder:
    async def test_an_abandoned_checklist_gets_one_nudge_naming_what_is_missing(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """`Completa tu configuracion` tells somebody who thought they had
        finished nothing at all."""
        _, tenant = await _tenant(api, mail, db)
        # Anchored to real time, not to NOW: `updated_at` is written by the
        # database at wall-clock time, so idleness can only be expressed
        # relative to that.
        later = datetime.now(UTC) + timedelta(hours=WIZARD_IDLE_HOURS + 1)

        await run_wizard_reminder(db, email_provider=mail, now=later)
        body = mail.last.text
        assert "Completar los datos de tu negocio" in body
        assert _notifications(db, tenant.id) == {"wizard_reminder"}

    async def test_it_is_sent_only_once_ever(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """One reminder is a service; a series of them is the reason people
        filter your domain."""
        await _tenant(api, mail, db)
        # Anchored to real time, not to NOW: `updated_at` is written by the
        # database at wall-clock time, so idleness can only be expressed
        # relative to that.
        later = datetime.now(UTC) + timedelta(hours=WIZARD_IDLE_HOURS + 1)

        await run_wizard_reminder(db, email_provider=mail, now=later)
        sent = len(mail.sent)
        await run_wizard_reminder(db, email_provider=mail, now=later + timedelta(days=1))
        assert len(mail.sent) == sent

    async def test_a_tenant_still_working_is_not_nudged(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        """Somebody who signed up an hour ago and is still filling the wizard
        does not need an email about it."""
        _, tenant = await _tenant(api, mail, db)
        # Scoped to this tenant, never to `outcome.acted`. That counter covers
        # every tenant in the database, so on any long-lived database - the
        # development one this suite shares with the manual demo - it reports
        # the hundreds of idle sign-ups left behind by earlier runs and the
        # assertion fails for a reason that has nothing to do with the rule
        # under test. See the rule at the top of tests/conftest.py.
        await run_wizard_reminder(db, email_provider=mail, now=datetime.now(UTC))
        assert _notifications(db, tenant.id) == set()
