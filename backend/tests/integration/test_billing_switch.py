"""Platform billing off (`BILLING_ENFORCED=false`, the default for now).

Giannier tests the product with his own businesses before Leo sells it: the
charging is built and covered by its own tests (which run with it on, see the
`billing_on` fixture), but switched off. Off means nothing expires, pauses or
limits a tenant; usage is still counted so it can be seen.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.business import AgentConfig
from app.services.team import TeamService
from app.services.trial import agent_may_reply, due_notice, is_expired, record_ai_message
from app.workers.trial_lifecycle import run_trial_enforcer, run_trial_notifier
from tests.integration.test_trial_lifecycle import NOW, _quota, _tenant

AUTH = "/api/v1/auth"
TENANT = "/api/v1/tenant"


@pytest.fixture(autouse=True)
def billing_off(billing_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "billing_enforced", False)


def test_the_default_is_off() -> None:
    """Read off the field, not off `get_settings()` (which a `.env` can change)."""
    from app.core.config import Settings

    assert Settings.model_fields["billing_enforced"].default is False


class TestTheAgentKeepsAnswering:
    async def test_a_trial_over_its_quota_may_still_reply(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
        tenant.ai_messages_used = _quota(db, tenant)
        db.commit()

        assert agent_may_reply(db, tenant) is True

    async def test_usage_is_still_counted(self, api: AsyncClient, mail: Any, db: Session) -> None:
        _, tenant = await _tenant(api, mail, db)
        before = tenant.ai_messages_used

        result = record_ai_message(db, tenant.id, channel="whatsapp")

        assert result.counted is True and result.used == before + 1

    async def test_a_tenant_paused_by_hand_stays_paused(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        db.add(AgentConfig(tenant_id=tenant.id, is_active=True))
        tenant.status = "paused"
        db.commit()

        assert agent_may_reply(db, tenant) is False


class TestTheTrialNeverEnds:
    async def test_an_expired_trial_is_not_paused_nor_warned(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        tenant.trial_ends_at = NOW - timedelta(days=2)
        tenant.ai_messages_used = _quota(db, tenant)
        db.commit()
        sent_before = len(mail.sent)

        enforced = await run_trial_enforcer(db, email_provider=mail, now=NOW)
        notified = await run_trial_notifier(db, email_provider=mail, now=NOW)

        db.refresh(tenant)
        assert tenant.status == "trial"
        assert (enforced.acted, notified.acted) == (0, 0)
        assert len(mail.sent) == sent_before
        assert is_expired(db, tenant, now=NOW) is False
        assert due_notice(db, tenant, now=NOW) is None


class TestTheLimitsAreNotEnforced:
    async def test_a_team_can_invite_past_its_seats(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, tenant = await _tenant(api, mail, db)
        team = TeamService(db, tenant.id)
        owner_id = team.list_members()[0].user_id
        limit = team.seat_usage().limit

        for _ in range(limit + 1):
            team.invite(
                email=f"persona-{uuid.uuid4().hex[:10]}@example.com",
                role="operator",
                invited_by=owner_id,
            )

        assert team.seat_usage().used > limit

    async def test_the_same_phone_can_open_a_second_business(self, api: AsyncClient) -> None:
        """One trial per phone protects a free offer that does not exist yet:
        the owner tests several of his own businesses from one number."""
        phone = f"+519{uuid.uuid4().int % 10**8:08d}"
        for _ in range(2):
            body = {
                "full_name": "Giannier",
                "email": f"g-{uuid.uuid4().hex[:12]}@example.com",
                "password": "una-clave-suficientemente-larga",
                "phone_e164": phone,
                "business_name": f"Negocio {uuid.uuid4().hex[:6]}",
            }
            response = await api.post(f"{AUTH}/register", json=body)
            assert response.status_code == 201, response.text


class TestThePanelSaysSo:
    async def test_the_tenant_and_its_usage_say_billing_is_off(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers, _ = await _tenant(api, mail, db)

        tenant = (await api.get(TENANT, headers=headers)).json()
        usage = (await api.get(f"{TENANT}/usage", headers=headers)).json()

        assert tenant["cobro_activo"] is False
        assert usage["cobro_activo"] is False
