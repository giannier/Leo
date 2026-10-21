"""Origin on the lead and in the results; hot leads go to the person chosen (019, F and E)."""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.crm import Lead
from app.models.tenant import HOT_LEAD
from app.models.user import User
from app.process.templates.base import Stage
from app.repositories.notification import TenantNotificationRepository
from app.services.channel import ConversationResponder
from app.services.metrics.origins import lead_origins
from app.services.tenant_alerts import TenantAlerts
from app.services.webchat import open_session
from tests.integration.test_crm_leads import _leads, _save, _user
from tests.integration.test_crm_leads import _setup as _lead_setup
from tests.integration.test_turn_pipeline import _conversation, _tenant


class TestTheOriginReachesTheLead:
    def test_the_web_chat_link_marks_the_visitor_and_the_lead_takes_it(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        visitor = open_session(db, tenant, origen="ig")
        contact = db.get(Contact, visitor.contact_id)
        assert contact is not None and contact.origen == "Instagram"

        plain = db.get(Contact, open_session(db, tenant).contact_id)
        assert plain is not None and plain.origen == "Chat web"

    def test_a_lead_born_from_a_customer_with_an_origin_has_it(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None
        contact.origen = "TikTok"
        db.flush()

        _save(
            states,
            conversation,
            Stage.ASESORIA,
            derivado={"motivo": "asesoria", "hasta": datetime.now(UTC).isoformat()},
        )

        (lead,) = _leads(db, tenant)
        assert lead.origen == "TikTok"

    def test_an_ad_wins_over_the_customer_origin(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None
        contact.origen = "TikTok"
        db.flush()

        _save(
            states,
            conversation,
            Stage.PEDIDO,
            entrada_anuncio={"ad_id": "a1", "headline": "Oferta de octubre"},
        )

        (lead,) = _leads(db, tenant)
        assert lead.origen == "Anuncio: Oferta de octubre"

    def test_the_customer_s_first_words_set_it_once(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        conversation = _conversation(db, tenant, channel="whatsapp")
        responder = ConversationResponder(db, tenant.id, llm=None)  # type: ignore[arg-type]
        from app.models.conversation import Message

        def said(text: str) -> Message:
            return Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content=text,
                msg_type="text",
            )

        responder._learn_origin(conversation.id, [said("Hola, te escribo desde Instagram")])
        responder._learn_origin(conversation.id, [said("ahora vengo de tiktok")])

        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None and contact.origen == "Instagram"


class TestTheResults:
    def test_leads_by_origin_with_how_many_were_won(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        today = datetime.now(UTC).date()
        stage_tenant, conversation, states = _lead_setup(db)
        _save(
            states,
            conversation,
            Stage.ASESORIA,
            derivado={"motivo": "asesoria", "hasta": datetime.now(UTC).isoformat()},
        )
        (base,) = _leads(db, stage_tenant)
        base.origen = "Instagram"
        base.status = "won"
        db.flush()
        for origin in ("Instagram", "TikTok", None):
            clone = Lead(
                tenant_id=stage_tenant.id,
                contact_id=_conversation(db, stage_tenant, channel="whatsapp").contact_id,
                stage_id=base.stage_id,
                origen=origin,
                status="active",
            )
            db.add(clone)
        db.flush()

        report = lead_origins(db, stage_tenant.id, today - timedelta(days=1), today)

        assert report[0] == {"origen": "Instagram", "leads": 2, "ganados": 1}
        assert {r["origen"] for r in report} == {"Instagram", "TikTok", "Directo"}
        assert lead_origins(db, tenant.id, today - timedelta(days=1), today) == []


class TestHotLeadsGoToThePersonChosen:
    def _hot(self, db: Session, *, member: bool = True) -> tuple[Any, Lead, uuid.UUID]:
        tenant, conversation, states = _lead_setup(db)
        person = _user(db)
        if member:
            from app.models.tenant import TenantUser

            db.add(
                TenantUser(tenant_id=tenant.id, user_id=person, role="operator", status="active")
            )
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.destino_leads = person
        db.flush()
        _save(
            states,
            conversation,
            Stage.IDENTIFICAR,
            lead_calificado={
                "interes": "alto",
                "motivo": "pide propuesta",
                "respuestas": {},
                "en": datetime.now(UTC).isoformat(),
            },
        )
        (lead,) = _leads(db, tenant)
        return tenant, lead, person

    def test_it_is_assigned_and_leaves_a_mark_in_the_history(self, db: Session) -> None:
        tenant, lead, person = self._hot(db)

        assert lead.score == "caliente" and lead.assigned_to == person

    def test_somebody_who_left_the_team_gets_nothing(self, db: Session) -> None:
        _, lead, _ = self._hot(db, member=False)

        assert lead.score == "caliente" and lead.assigned_to is None

    def test_a_lead_a_person_already_has_stays_with_them(self, db: Session) -> None:
        tenant, conversation, states = _lead_setup(db)
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.destino_leads = _user(db)
        db.flush()
        _save(
            states,
            conversation,
            Stage.ASESORIA,
            derivado={"motivo": "asesoria", "hasta": datetime.now(UTC).isoformat()},
        )
        (lead,) = _leads(db, tenant)
        mine = _user(db)
        lead.assigned_to = mine
        db.flush()

        _save(
            states,
            conversation,
            Stage.ASESORIA,
            lead_calificado={
                "interes": "alto",
                "respuestas": {},
                "en": datetime.now(UTC).isoformat(),
            },
        )

        assert lead.assigned_to == mine

    def test_the_destination_is_emailed_as_well_as_the_owner(self, db: Session) -> None:
        tenant = _tenant(db, template="servicios")
        owner = User(
            email=f"o-{uuid.uuid4().hex[:6]}@example.com", password_hash="x", full_name="Dueña"
        )
        chosen = User(
            email=f"d-{uuid.uuid4().hex[:6]}@example.com", password_hash="x", full_name="Bruno"
        )
        db.add_all([owner, chosen])
        db.flush()
        from app.models.tenant import TenantUser

        db.add(TenantUser(tenant_id=tenant.id, user_id=owner.id, role="owner", status="active"))
        config = db.get(AgentConfig, tenant.id)
        assert config is not None
        config.destino_leads = chosen.id
        db.flush()
        alerts = TenantAlerts(db, tenant.id)

        alerts.hot_lead(_conversation(db, tenant, channel="whatsapp").id, resumen="Pide propuesta")

        assert sorted(m.to[0] for m in alerts.pending_emails) == sorted([owner.email, chosen.email])
        (notice,) = TenantNotificationRepository(db, tenant.id).recent(HOT_LEAD)
        assert notice.payload["resumen"] == "Pide propuesta"
        assert db.execute(select(User).where(User.id == chosen.id)).scalar_one() is chosen


class TestChoosingTheDestination:
    async def test_it_has_to_be_somebody_of_the_team_and_can_be_cleared(
        self, api: AsyncClient, mail: Any
    ) -> None:
        from tests.integration.test_assistant_config_api import TENANT, _account

        _, headers = await _account(api, mail)
        team = (await api.get(f"{TENANT}/team", headers=headers)).json()
        owner = team["members"][0]["user_id"]

        chosen = await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"destino_leads": owner}
        )
        stranger = await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"destino_leads": str(uuid.uuid4())}
        )
        untouched = await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"tone": "cercano"}
        )
        cleared = await api.put(
            f"{TENANT}/agent-config", headers=headers, json={"destino_leads": None}
        )

        assert chosen.json()["destino_leads"] == owner
        assert stranger.status_code == 400 and stranger.json()["fields"]["destino_leads"]
        assert untouched.json()["destino_leads"] == owner  # not sent: stays
        assert cleared.json()["destino_leads"] is None
