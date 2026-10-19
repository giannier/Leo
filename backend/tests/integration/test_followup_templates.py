"""A follow-up after Meta's window closed goes as the business's approved utility template,
without the model, at most once per conversation and twice a month per contact (011, C-110).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.llm import MockLLMProvider
from app.services import followups as module
from app.services.campaigns.sender import TemplateSender
from app.services.followups import FollowUps
from tests.integration.campaign_fixtures import FakeWhatsApp, template
from tests.integration.test_followups import _ready

LATER = timedelta(hours=30)
BODY = (
    "Hola, tu pedido en {{negocio}} está pendiente de pago. Si necesitas ayuda para "
    "completarlo, responde a este mensaje."
)


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "local_time", lambda now, zone: time(12, 0))


def _with_template(
    db: Session, *, status: str = "approved", category: str = "utility"
) -> tuple[Tenant, Conversation]:
    tenant, conversation = _ready(db, max_intentos=3)
    db.add(
        WhatsAppNumber(
            tenant_id=tenant.id,
            phone_e164="+51911122233",
            display_name="Capacita",
            provider="ycloud",
            status="connected",
            credentials_enc=encrypt_secret(
                json.dumps(
                    {
                        "waba_id": "w1",
                        "phone_number_id": "112233445566",
                        "api_key": "yc_live_abc123",
                    }
                )
            ),
        )
    )
    used = template(db, tenant, body=BODY, category=category, status=status, label="Pago pendiente")
    config = db.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant.id)).scalar_one()
    config.seguimiento_plantilla_id = used.id
    db.flush()
    return tenant, conversation


class TestOutsideTheWindow:
    async def test_the_template_goes_without_the_model(self, db: Session) -> None:
        tenant, conversation = _with_template(db)
        llm = MockLLMProvider()

        result = await FollowUps(db, tenant.id, llm=llm).run(
            conversation.id, now=datetime.now(UTC) + LATER
        )

        assert result.outcome == "plantilla"
        assert result.template is not None and result.filled is not None
        assert result.filled.values == ["Capacita"]
        assert llm.chat_calls == []

        fake = FakeWhatsApp()
        assert result.contact is not None
        message = await TemplateSender(db, tenant.id, provider=fake).send(
            result.contact,
            result.template,
            result.filled,
            kind="seguimiento",
            conversation=conversation,
        )
        assert message.conversation_id == conversation.id and message.msg_type == "template"
        assert fake.templates[0]["values"] == ["Capacita"]

        # One per conversation (C-110): the next one waits for the customer.
        again = await FollowUps(db, tenant.id, llm=llm).run(
            conversation.id, now=datetime.now(UTC) + LATER + timedelta(hours=10)
        )
        assert again.outcome == "ventana"

    @pytest.mark.parametrize(
        ("status", "category"), [("pending", "utility"), ("approved", "marketing")]
    )
    async def test_only_an_approved_utility_template(
        self, db: Session, status: str, category: str
    ) -> None:
        tenant, conversation = _with_template(db, status=status, category=category)
        result = await FollowUps(db, tenant.id, llm=MockLLMProvider()).run(
            conversation.id, now=datetime.now(UTC) + LATER
        )
        assert result.outcome == "ventana"

    async def test_twice_a_month_per_contact(self, db: Session) -> None:
        tenant, conversation = _with_template(db)
        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None
        for _ in range(2):
            other = Conversation(
                tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="closed"
            )
            db.add(other)
            db.flush()
            db.add(
                Message(
                    tenant_id=tenant.id,
                    conversation_id=other.id,
                    direction="outbound",
                    author="system",
                    content="Hola",
                    msg_type="template",
                    payload={"motivo": "seguimiento"},
                )
            )
        db.flush()
        result = await FollowUps(db, tenant.id, llm=MockLLMProvider()).run(
            conversation.id, now=datetime.now(UTC) + LATER
        )
        assert result.outcome == "ventana"
