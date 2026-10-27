"""Advanced analytics, A/B campaigns, the monthly report and Leo's NPS (Feature 013)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import CsatSurvey, NpsResponse
from app.models.business import Product
from app.models.campaigns import CampaignRecipient
from app.models.conversation import Conversation
from app.models.crm import Lead, LeadEvent, PipelineStage
from app.models.metrics import DashboardSettings
from app.models.orders import OrderItem
from app.models.tenant import Tenant
from app.repositories.crm import PipelineStageRepository
from app.repositories.orders import OrderRepository
from app.services.analytics import nps
from app.services.analytics.monthly import build, claim, previous_month, render
from app.services.analytics.report import funnel, products, satisfaction
from app.services.campaigns.report import campaign_report
from app.services.campaigns.service import CampaignRunner, CampaignService
from tests.integration.campaign_fixtures import FakeWhatsApp, business, customer, template
from tests.integration.test_crm_leads import _user
from tests.integration.test_orders_api import operator_for

TODAY = datetime.now(UTC).date()
NOON = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)


def _stage(db: Session, tenant: Tenant, semantic: str) -> uuid.UUID:
    PipelineStageRepository(db, tenant.id).ensure_defaults()
    return db.execute(
        select(PipelineStage.id).where(
            PipelineStage.tenant_id == tenant.id, PipelineStage.semantic == semantic
        )
    ).scalar_one()


def _journey(
    db: Session,
    tenant: Tenant,
    *,
    reach: str | None,
    product: str | None = None,
    origen: str | None = None,
) -> Conversation:
    """A real conversation; with a lead that reached `reach` (None: no lead)."""
    contact = customer(db, tenant)
    thread = Conversation(
        tenant_id=tenant.id, channel="whatsapp", contact_id=contact.id, status="open"
    )
    db.add(thread)
    db.flush()
    if reach is None:
        return thread
    status = "won" if reach == "ganado" else "active"
    lead = Lead(
        tenant_id=tenant.id,
        contact_id=contact.id,
        conversation_id=thread.id,
        stage_id=_stage(db, tenant, reach),
        status=status,
        product_interest=product,
        origen=origen,
    )
    db.add(lead)
    db.flush()
    path = ["nuevo", "interesado", "por_pagar", "ganado"]
    for stage in path[: path.index(reach) + 1]:
        db.add(
            LeadEvent(
                tenant_id=tenant.id, lead_id=lead.id, actor="agent", cause="turno", to_stage=stage
            )
        )
    db.flush()
    return thread


def _sale(db: Session, tenant: Tenant, thread: Conversation, item: str, total: str) -> None:
    order = OrderRepository(db, tenant.id).add_numbered(
        conversation_id=thread.id,
        contact_id=thread.contact_id,
        moneda="PEN",
        subtotal=Decimal(total),
        total=Decimal(total),
        estado="confirmado",
    )
    order.confirmed_at = datetime.now(UTC)
    db.add(
        OrderItem(
            tenant_id=tenant.id,
            order_id=order.id,
            nombre=item,
            precio_unitario=Decimal(total),
            cantidad=2,
            subtotal=Decimal(total),
        )
    )
    db.flush()


class TestFunnel:
    def test_the_steps_and_their_rates(self, db: Session) -> None:
        tenant, _ = business(db)
        _journey(db, tenant, reach=None)
        _journey(db, tenant, reach=None)
        _journey(db, tenant, reach="nuevo")
        _journey(db, tenant, reach="por_pagar")
        _journey(db, tenant, reach="ganado")
        report = funnel(db, tenant.id, TODAY - timedelta(days=6), TODAY)
        now = report["actual"]
        assert (now["conversaciones"], now["con_intencion"], now["por_pagar"], now["ganados"]) == (
            5,
            3,
            2,
            1,
        )
        assert (
            now["tasa_intencion"] == 0.6 and now["tasa_cierre"] == 0.5 and now["conversion"] == 0.2
        )
        assert report["anterior"]["conversaciones"] == 0
        assert report["por_origen"]["Directo"] == {"conversaciones": 5, "ganados": 1}
        assert sum(day["conversaciones"] for day in report["por_dia_semana"].values()) == 5

    def test_an_organic_origin_is_not_counted_as_an_ad(self, db: Session) -> None:
        """The report prefixed «Anuncio:» to every origin: «Anuncio: Instagram» for a customer who
        came on their own, «Anuncio: Anuncio: Oferta» for one who came from a paid ad."""
        tenant, _ = business(db)
        _journey(db, tenant, reach="nuevo", origen="Instagram")
        _journey(db, tenant, reach="ganado", origen="Anuncio: Oferta de octubre")

        report = funnel(db, tenant.id, TODAY - timedelta(days=6), TODAY)

        assert report["por_origen"]["Instagram"] == {"conversaciones": 1, "ganados": 0}
        assert report["por_origen"]["Anuncio: Oferta de octubre"] == {
            "conversaciones": 1,
            "ganados": 1,
        }


class TestProducts:
    def test_sold_asked_and_out_of_stock(self, db: Session) -> None:
        tenant, _ = business(db)
        sold = _journey(db, tenant, reach="ganado", product="Torta")
        _sale(db, tenant, sold, "Torta", "80.00")
        _journey(db, tenant, reach="nuevo", product="Pie de limón")
        _journey(db, tenant, reach="nuevo", product="Pie de limón")
        _journey(db, tenant, reach="nuevo", product="Alfajores")
        db.add(Product(tenant_id=tenant.id, name="Alfajores", price=Decimal("10"), stock=0))
        db.flush()
        report = products(db, tenant.id, TODAY - timedelta(days=1), TODAY, money=True)
        assert report["vendidos"] == [
            {"producto": "Torta", "unidades": 2, "monto": {"PEN": "80.00"}}
        ]
        assert report["ticket_promedio"] == {"PEN": "80.00"}
        assert report["consultados_sin_venta"][0] == {"producto": "Pie de limón", "consultas": 2}
        assert report["agotados_pedidos"] == [{"producto": "Alfajores", "consultas": 1}]
        hidden = products(db, tenant.id, TODAY - timedelta(days=1), TODAY, money=False)
        assert "ticket_promedio" not in hidden and "monto" not in hidden["vendidos"][0]


class TestSatisfaction:
    def test_average_and_people_with_five_answers(self, db: Session) -> None:
        tenant, _ = business(db)
        thread = _journey(db, tenant, reach=None)
        ana, luis = _user(db), _user(db)
        scores = [(None, 5), (None, 4), (luis, 3)] + [(ana, 5)] * 5
        for operator, score in scores:
            db.add(
                CsatSurvey(
                    tenant_id=tenant.id,
                    conversation_id=thread.id,
                    contact_id=thread.contact_id,
                    operator_id=operator,
                    trigger="ganado",
                    status="answered",
                    score=score,
                    comment="Rápido" if score == 3 else None,
                    answered_at=datetime.now(UTC),
                )
            )
        db.flush()
        report = satisfaction(db, tenant.id, TODAY - timedelta(days=1), TODAY, by_person=True)
        assert report["con_nota"] == 8 and report["promedio"] == 4.62
        assert report["distribucion"]["5"] == 6
        assert report["asistente"] == {"respuestas": 2, "promedio": 4.5}
        people = {p["respuestas"]: p["promedio"] for p in report["por_persona"]}
        assert people == {5: 5.0, 1: None}  # one answer says nothing about Luis
        assert report["comentarios"][0]["comentario"] == "Rápido"
        assert "por_persona" not in satisfaction(db, tenant.id, TODAY, TODAY, by_person=False)


class TestAB:
    async def test_half_and_half_with_a_small_sample_warning(self, db: Session) -> None:
        tenant, _ = business(db)
        for _ in range(8):
            customer(db, tenant)
        a = template(db, tenant, label="Promo A")
        b = template(
            db, tenant, label="Promo B", body="Hola {{nombre}}, solo hoy 2x1 en {{negocio}}."
        )
        service = CampaignService(db, tenant.id)
        campaign = service.create(
            {"name": "A/B", "template_id": a.id, "template_b_id": b.id, "segment": {}}, by=None
        )
        service.schedule(campaign, now=NOON)
        fake = FakeWhatsApp()
        await CampaignRunner(db, provider=fake, storage=None, now=NOON).tick()
        rows = list(
            db.execute(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
            ).scalars()
        )
        assert {r.variant for r in rows} == {"A", "B"}
        assert all((r.variant == "B") == bool(r.contact_id.int % 2) for r in rows)
        names = {t["name"] for t in fake.templates}
        assert names == {a.name, b.name}
        report = campaign_report(db, campaign, money=False)["variantes"]
        assert report["A"]["enviados"] + report["B"]["enviados"] == 8
        assert report["A"]["muestra_pequena"] and report["B"]["muestra_pequena"]


class TestMonthly:
    def test_claimed_once_and_written_for_people(self, db: Session) -> None:
        tenant, _ = business(db)
        first, last = previous_month(date(2026, 11, 1))
        assert (first, last) == (date(2026, 10, 1), date(2026, 10, 31))
        assert claim(db, tenant.id, first) is True
        assert claim(db, tenant.id, first) is False
        settings = db.get(DashboardSettings, tenant.id)
        assert settings is not None
        settings.reporte_mensual = False
        db.flush()
        assert claim(db, tenant.id, date(2026, 11, 1)) is False

        data = build(db, tenant.id, first, last)
        assert data["mes"] == "octubre 2026"
        email = render("Panadería <Rosa>", data, to="ana@x.pe", panel_url="https://leo/resultados")
        assert (
            "Panadería &lt;Rosa&gt;" in email.html
            and email.subject == "Tu mes en Leo: octubre 2026"
        )


class TestNps:
    def test_cycles(self) -> None:
        signed = date(2026, 1, 1)
        assert nps.cycle_for(signed, date(2026, 1, 20)) is None
        assert nps.cycle_for(signed, date(2026, 2, 1)) == date(2026, 1, 31)
        assert nps.cycle_for(signed, date(2026, 5, 1)) == date(2026, 5, 1)
        assert nps.nps([10, 9, 8, 3]) == 25

    def test_asked_once_per_cycle_to_owners(self, db: Session) -> None:
        tenant, _ = business(db)
        tenant.created_at = datetime.now(UTC) - timedelta(days=40)
        user = _user(db)
        db.flush()
        assert nps.due(db, tenant.id, user, "operator", TODAY) is None
        cycle = nps.due(db, tenant.id, user, "owner", TODAY)
        assert cycle is not None
        nps.record(db, tenant.id, user, cycle, score=None, comment=None)  # closed the card
        assert nps.due(db, tenant.id, user, "owner", TODAY) is None
        row = db.execute(select(NpsResponse).where(NpsResponse.user_id == user)).scalar_one()
        assert row.dismissed and row.score is None


class TestApi:
    async def test_settings_roles_and_isolation(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        account, owner, operator = await operator_for(api, mail, db)
        _, other_owner, _ = await operator_for(api, mail, db)
        base = "/api/v1/tenant/analytics"
        assert (await api.get(f"{base}/settings", headers=owner)).json() == {
            "csat_activo": False,
            "reporte_mensual": True,
        }
        saved = await api.put(
            f"{base}/settings", headers=owner, json={"csat_activo": True, "reporte_mensual": False}
        )
        assert saved.status_code == 200
        assert (
            await api.put(
                f"{base}/settings",
                headers=operator,
                json={"csat_activo": False, "reporte_mensual": True},
            )
        ).status_code == 403
        assert (await api.get(f"{base}/settings", headers=other_owner)).json()[
            "csat_activo"
        ] is False

        tenant = db.get(Tenant, uuid.UUID(str(account["tenant_id"])))
        assert tenant is not None
        thread = _journey(db, tenant, reach="ganado", product="Torta")
        _sale(db, tenant, thread, "Torta", "50.00")
        db.commit()
        mine = (await api.get(f"{base}/funnel", headers=owner)).json()
        assert mine["actual"]["ganados"] == 1
        assert (await api.get(f"{base}/funnel", headers=other_owner)).json()["actual"][
            "ganados"
        ] == 0
        assert "ticket_promedio" in (await api.get(f"{base}/products", headers=owner)).json()
        assert "ticket_promedio" not in (await api.get(f"{base}/products", headers=operator)).json()
        assert "por_persona" in (await api.get(f"{base}/csat", headers=owner)).json()
        assert "por_persona" not in (await api.get(f"{base}/csat", headers=operator)).json()
        assert (
            await api.get(f"{base}/funnel", headers=owner, params={"desde": "2024-01-01"})
        ).status_code == 400

        preview = await api.get("/api/v1/tenant/reports/monthly/202610", headers=owner)
        assert preview.status_code == 200 and "Así le fue" in preview.text
        assert (await api.get("/api/v1/tenant/nps", headers=owner)).json() == {
            "preguntar": False,
            "ciclo": None,
        }
        assert (await api.get("/api/v1/platform-admin/nps", headers=owner)).status_code == 404
