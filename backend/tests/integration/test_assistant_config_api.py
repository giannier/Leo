"""The assistant's configuration API (004 §1–§3 with the 017 layers; TASK-434 to 437)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_storage_provider
from app.core.config import get_settings
from app.models.knowledge import KnowledgeSource
from app.models.orders import Order
from app.models.tenant import TenantUser
from app.orders.proofs import ProofPipeline
from app.process.service import DEFAULT_CACHE
from app.providers.llm import MockLLMProvider
from app.providers.storage.mock import MockStorageProvider
from app.services.knowledge import INDEX_JOB
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.integration.test_team import _owner

A = "/api/v1/assistant"
TENANT = "/api/v1/tenant"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
EXE = b"MZ\x90\x00" + b"\x00" * 200


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recording
    return recording


@pytest.fixture
def storage(app: FastAPI) -> MockStorageProvider:
    store = MockStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: store
    return store


async def _account(api: AsyncClient, mail: Any) -> tuple[uuid.UUID, dict[str, str]]:
    account, headers = await _owner(api, mail)
    return uuid.UUID(account["tenant_id"]), headers


def _role(db: Session, tenant_id: uuid.UUID, role: str, *, reviewer: bool = False) -> None:
    db.execute(
        update(TenantUser)
        .where(TenantUser.tenant_id == tenant_id)
        .values(role=role, puede_revisar=reviewer)
    )
    db.flush()


class TestBusinessInfo:
    async def test_a_section_round_trips_and_a_bad_one_is_refused(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _account(api, mail)
        body = {
            "hace_delivery": True,
            "zonas": [{"nombre": "Centro", "costo": "8.00", "tiempo": "mismo día"}],
            "envio_gratis_desde": "150.00",
            "recojo_en_tienda": False,
        }

        saved = await api.put(f"{A}/business-info/entregas", headers=headers, json=body)
        read = await api.get(f"{A}/business-info/entregas", headers=headers)
        bad = await api.put(f"{A}/business-info/entregas", headers=headers, json={"x": 1})

        assert saved.status_code == 200, saved.text
        assert read.json()["data"]["zonas"][0]["nombre"] == "Centro"
        assert bad.status_code == 422 and bad.json()["error"] == "validation_error"


class TestFaqs:
    async def test_a_faq_is_indexed_reindexed_and_removed(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)

        created = await api.post(
            f"{A}/faqs", headers=headers, json={"pregunta": "¿Hacen envíos?", "respuesta": "Sí."}
        )
        faq = created.json()
        first_source = queue.last_source_id
        edited = await api.patch(
            f"{A}/faqs/{faq['id']}", headers=headers, json={"respuesta": "Sí, a todo Lima."}
        )
        second_source = queue.last_source_id
        deleted = await api.delete(f"{A}/faqs/{faq['id']}", headers=headers)

        assert created.status_code == 201 and faq["indexada"] is True
        assert [job for job, _ in queue.jobs] == [INDEX_JOB, INDEX_JOB]
        assert edited.json()["respuesta"] == "Sí, a todo Lima."
        assert first_source != second_source
        assert deleted.status_code == 204
        for source_id in (first_source, second_source):
            source = db.get(KnowledgeSource, source_id)
            assert source is not None and source.deleted_at is not None
        assert (await api.get(f"{A}/faqs", headers=headers)).json()["items"] == []


class TestPromotions:
    async def test_create_validate_and_delete(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _account(api, mail)
        body = {"nombre": "10 %", "tipo": "porcentaje", "valor": "10.00"}

        created = await api.post(f"{A}/promotions", headers=headers, json=body)
        bad = await api.post(
            f"{A}/promotions",
            headers=headers,
            json={"nombre": "2x1", "tipo": "nxm", "n": 1, "m": 2},
        )
        closing = await api.post(
            f"{A}/promotions",
            headers=headers,
            json={**body, "uso": "cierre"},
        )
        listed = (await api.get(f"{A}/promotions", headers=headers)).json()["items"]
        deleted = await api.delete(f"{A}/promotions/{created.json()['id']}", headers=headers)

        assert created.status_code == 201 and Decimal(created.json()["valor"]) == Decimal("10")
        assert bad.status_code in (400, 422)
        assert closing.status_code in (400, 422)
        assert [p["nombre"] for p in listed] == ["10 %"]
        assert deleted.status_code == 204
        assert (await api.get(f"{A}/promotions", headers=headers)).json()["items"] == []

    async def test_a_used_promotion_is_switched_off_not_deleted(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        from tests.integration.test_orders_api import seed_order

        tenant_id, headers = await _account(api, mail)
        promo = (
            await api.post(
                f"{A}/promotions",
                headers=headers,
                json={"nombre": "10 %", "tipo": "porcentaje", "valor": "10.00"},
            )
        ).json()
        order, _ = seed_order(db, str(tenant_id))
        db.execute(update(Order).where(Order.id == order.id).values(promotion_id=promo["id"]))
        db.flush()

        await api.delete(f"{A}/promotions/{promo['id']}", headers=headers)

        items = (await api.get(f"{A}/promotions", headers=headers)).json()["items"]
        assert [(p["id"], p["is_active"]) for p in items] == [(promo["id"], False)]


class TestGreetingLength:
    GREETING = (
        "¡Hola! 👋 ¡Bienvenido a Zentek360!\n\nSoy tu asistente virtual y estoy aquí para "
        "ayudarte. Puedo brindarte información sobre nuestros servicios de desarrollo web, "
        "sistemas a medida, automatizaciones, inteligencia artificial y hosting.\n\nCuéntame, "
        "¿qué necesitas para llevar tu negocio al siguiente nivel? 🚀"
    )

    async def test_a_real_welcome_message_fits(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _account(api, mail)
        assert len(self.GREETING) > 120

        saved = await api.put(f"{A}/behavior", headers=headers, json={"saludo": self.GREETING})

        assert saved.status_code == 200, saved.text
        assert saved.json()["saludo"] == self.GREETING

    async def test_the_cap_is_400(self, api: AsyncClient, mail: Any) -> None:
        _, headers = await _account(api, mail)

        exact = await api.put(f"{A}/behavior", headers=headers, json={"saludo": "a" * 400})
        over = await api.put(f"{A}/behavior", headers=headers, json={"saludo": "a" * 401})

        assert exact.status_code == 200
        assert over.status_code == 400


class TestBehavior:
    async def test_the_profile_by_form_and_system_messages(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _account(api, mail)

        saved = await api.put(
            f"{A}/behavior",
            headers=headers,
            json={
                "tratamiento": "usted",
                "emojis": "no",
                "saludo": "¡Bienvenido!",
                "politica_descuentos": "negociable",
                "descuento_maximo_pct": "10",
                "max_situaciones": 3,
                "seguimiento": {"activo": True, "horas": 6, "max_intentos": 2},
                "mensajes_sistema": {
                    "pago_confirmado": "Listo {nombre_cliente}, pedido #{numero}."
                },
            },
        )
        bad_placeholder = await api.put(
            f"{A}/behavior",
            headers=headers,
            json={"mensajes_sistema": {"pago_confirmado": "Hola {inventado}"}},
        )
        reset = await api.put(
            f"{A}/behavior", headers=headers, json={"mensajes_sistema": {"pago_confirmado": None}}
        )

        body = saved.json()
        assert saved.status_code == 200, saved.text
        assert (body["tratamiento"], body["emojis"], body["saludo"]) == (
            "usted",
            "no",
            "¡Bienvenido!",
        )
        assert Decimal(body["descuento_maximo_pct"]) == Decimal("10")
        assert body["seguimiento"] == {"activo": True, "horas": 6, "max_intentos": 2}
        assert body["mensajes_sistema"]["pago_confirmado"].startswith("Listo")
        assert bad_placeholder.json()["error"] == "unknown_placeholder"
        assert "Listo" not in reset.json()["mensajes_sistema"]["pago_confirmado"]

    async def test_levels_follow_the_plan_only_with_billing_on(
        self, api: AsyncClient, mail: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, headers = await _account(api, mail)

        refused = await api.put(
            f"{A}/behavior", headers=headers, json={"nivel_inteligencia": "mejor"}
        )
        monkeypatch.setattr(get_settings(), "billing_enforced", False)
        allowed = await api.put(
            f"{A}/behavior", headers=headers, json={"nivel_inteligencia": "mejor"}
        )

        assert refused.status_code == 403 and refused.json()["error"] == "plan_limit"
        assert allowed.status_code == 200
        assert allowed.json()["niveles_permitidos"] == ["economico", "medio", "mejor"]

    async def test_saving_invalidates_the_cached_configuration(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        from app.process.service import ProcessConfigService

        tenant_id, headers = await _account(api, mail)
        ProcessConfigService(db).effective(tenant_id)

        await api.put(f"{A}/behavior", headers=headers, json={"tratamiento": "usted"})

        assert DEFAULT_CACHE.get(tenant_id) is None

    async def test_objective_and_handover_rules_are_editable_here(
        self, api: AsyncClient, mail: Any
    ) -> None:
        """Feature 018, HU-05: no need to go back to the setup wizard."""
        _, headers = await _account(api, mail)
        before = (await api.get(f"{A}/behavior", headers=headers)).json()
        assert before["objective"] == "ambos"
        assert set(before["escalation_rules"]) == {
            "cliente_molesto",
            "pide_humano",
            "fuera_de_catalogo",
        }

        saved = await api.put(
            f"{A}/behavior",
            headers=headers,
            json={"objective": "vender", "escalation_rules": ["pide_humano", "pide_humano"]},
        )
        assert saved.status_code == 200
        assert saved.json()["objective"] == "vender"
        assert saved.json()["escalation_rules"] == ["pide_humano"]
        unknown = await api.put(
            f"{A}/behavior", headers=headers, json={"escalation_rules": ["inventado"]}
        )
        assert unknown.status_code == 400


class TestPaymentHolder:
    async def test_the_holder_is_what_a_receipt_is_compared_with(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, headers = await _account(api, mail)

        created = await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={
                "method": "yape",
                "instructions": "Yape al 999",
                "titular": "Escuela Capacita SAC",
            },
        )
        bad_qr = await api.put(
            f"{TENANT}/payment-methods/{created.json()['id']}",
            headers=headers,
            json={"media_asset_id": str(uuid.uuid4())},
        )
        pipeline = ProofPipeline(db, tenant_id, llm=MockLLMProvider(), storage=None)

        assert created.json()["titular"] == "Escuela Capacita SAC"
        assert bad_qr.status_code == 422 and bad_qr.json()["error"] == "invalid_media"
        assert pipeline._payees == ("Escuela Capacita SAC",)


class TestProcessAndRules:
    async def test_templates_process_and_switches(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        _, headers = await _account(api, mail)
        await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Capacita", "industry": "instituto"},
        )

        templates = (await api.get(f"{A}/templates", headers=headers)).json()["items"]
        changed = await api.put(
            f"{A}/process", headers=headers, json={"template_key": "certificaciones", "options": {}}
        )
        bad = await api.put(
            f"{A}/process",
            headers=headers,
            json={"template_key": "productos", "options": {"modalidad_pago": "x"}},
        )
        cobro = next(s for s in changed.json()["etapas"] if s["key"] == "COBRO")
        rule = cobro["reglas"][0]["rule_key"]
        switched = await api.put(
            f"{A}/rules/proceso/{rule}", headers=headers, json={"apagada": True}
        )
        preview = await api.get(f"{A}/prompt-preview", headers=headers, params={"stage": "COBRO"})
        unknown = await api.put(
            f"{A}/rules/proceso/COBRO.no_existe", headers=headers, json={"apagada": True}
        )

        assert {t["key"]: t["sugerida_por_rubro"] for t in templates} == {
            "productos": False,
            "certificaciones": True,
            "servicios": False,
        }
        assert changed.json()["template_key"] == "certificaciones"
        assert changed.json()["template_version"] == 2  # v2: migration 0033
        assert bad.status_code == 422 and bad.json()["error"] == "invalid_options"
        assert switched.json()["apagada"] is True
        assert cobro["reglas"][0]["texto"].split("\n")[0] not in preview.json()["prompt"]
        assert "RUBRO" in preview.json()["prompt"]
        assert unknown.status_code == 404

    async def test_the_rubro_view_with_its_news(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        from app.models.catalog import CatalogRule
        from app.repositories.catalog import CatalogRepository

        _, headers = await _account(api, mail)
        await api.put(
            f"{TENANT}/business-profile",
            headers=headers,
            json={"business_name": "Tienda", "industry": "retail"},
        )
        first = (await api.get(f"{A}/rubro", headers=headers)).json()
        catalog = CatalogRepository(db)
        draft = catalog.new_draft("rubro", "retail")
        db.add(CatalogRule(version_id=draft.id, orden=99, rule_key="rubro.nueva", texto="Nueva."))
        db.flush()
        catalog.publish(draft, by=None)

        second = (await api.get(f"{A}/rubro", headers=headers)).json()
        seen = await api.post(f"{A}/novedades/rubro/vistas", headers=headers)
        third = (await api.get(f"{A}/rubro", headers=headers)).json()

        assert first["key"] == "retail" and first["novedades"] is None
        assert len(first["faqs"]) == 5 and first["situaciones_sugeridas"]
        assert [r["rule_key"] for r in second["novedades"]["reglas"]] == ["rubro.nueva"]
        assert seen.status_code == 204
        assert third["novedades"] is None

    async def test_a_reviewer_switches_rules_and_an_operator_cannot(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        tenant_id, headers = await _account(api, mail)
        url = f"{A}/rules/proceso/COBRO.pedido_datos_completos"

        _role(db, tenant_id, "operator")
        refused = await api.put(url, headers=headers, json={"apagada": True})
        _role(db, tenant_id, "operator", reviewer=True)
        allowed = await api.put(url, headers=headers, json={"apagada": True})
        config = await api.put(f"{A}/process", headers=headers, json={"template_key": "productos"})

        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert config.status_code == 403  # reviewing is not configuring


class TestTeamReviewer:
    async def test_the_owner_grants_the_reviewer_permission(
        self, api: AsyncClient, mail: Any
    ) -> None:
        _, headers = await _account(api, mail)
        team = (await api.get(f"{TENANT}/team", headers=headers)).json()
        me = team["members"][0]

        changed = await api.put(
            f"{TENANT}/team/members/{me['id']}", headers=headers, json={"puede_revisar": True}
        )

        assert me["puede_revisar"] is True  # the owner always reviews
        assert changed.status_code == 200 and changed.json()["puede_revisar"] is True


class TestMedia:
    async def test_upload_replace_and_delete_by_content(
        self, api: AsyncClient, mail: Any, storage: MockStorageProvider
    ) -> None:
        _, headers = await _account(api, mail)
        form = {"clave": "qr_yape", "titulo": "QR de Yape", "cuando_usar": "Al enviar el pago"}

        ok = await api.post(
            f"{A}/media", headers=headers, data=form, files={"file": ("qr.png", PNG)}
        )
        renamed_exe = await api.post(
            f"{A}/media",
            headers=headers,
            data={**form, "clave": "otro"},
            files={"file": ("foto.jpg", EXE)},
        )
        too_big = await api.post(
            f"{A}/media",
            headers=headers,
            data={**form, "clave": "grande"},
            files={"file": ("big.jpg", JPG + b"\x00" * (6 * 1024 * 1024))},
        )
        taken = await api.post(
            f"{A}/media", headers=headers, data=form, files={"file": ("qr.png", PNG)}
        )
        asset = ok.json()
        replaced = await api.post(
            f"{A}/media/{asset['id']}/file", headers=headers, files={"file": ("qr.jpg", JPG)}
        )
        wrong_type = await api.post(
            f"{A}/media/{asset['id']}/file",
            headers=headers,
            files={"file": ("doc.pdf", b"%PDF-1.4" + b"\x00" * 50)},
        )
        renamed = await api.patch(f"{A}/media/{asset['id']}", headers=headers, json={"clave": "qr"})
        deleted = await api.delete(f"{A}/media/{asset['id']}", headers=headers)

        assert ok.status_code == 201, ok.text
        assert (asset["tipo"], asset["clave"]) == ("imagen", "qr_yape") and asset["preview_url"]
        assert renamed_exe.json()["error"] == "unsupported_file"
        assert too_big.json()["error"] == "file_too_large"
        assert taken.json()["error"] == "clave_taken"
        assert replaced.status_code == 200 and replaced.json()["filename"] == "qr.jpg"
        assert wrong_type.json()["error"] == "type_changed"
        assert renamed.json()["clave"] == "qr"
        assert deleted.status_code == 204


class TestOverview:
    async def test_an_empty_business_and_a_partial_one(
        self, api: AsyncClient, mail: Any, db: Session, queue: RecordingQueue
    ) -> None:
        _, headers = await _account(api, mail)

        empty = (await api.get(f"{A}/overview", headers=headers)).json()
        await api.post(
            f"{TENANT}/products", headers=headers, json={"name": "Polo", "price": "35.00"}
        )
        await api.post(
            f"{TENANT}/payment-methods",
            headers=headers,
            json={"method": "yape", "instructions": "Yape al 999"},
        )
        situation = await api.post(
            f"{A}/situations",
            headers=headers,
            json={"titulo": "Factura", "cuando_aplica": "Pide factura", "que_hace": "Pide el RUC"},
        )
        await api.post(f"{A}/situations/{situation.json()['id']}/activar", headers=headers)
        partial = (await api.get(f"{A}/overview", headers=headers)).json()

        steps = {s["paso"]: s["hecho"] for s in partial["activacion"]}
        assert empty["siguiente_paso"] == "productos"
        assert empty["progreso"] < partial["progreso"]
        assert steps["productos"] and steps["metodos_pago"] and steps["primera_situacion"]
        assert not steps["probar_simulador"] and not steps["conectar_whatsapp"]
        # No agent configuration yet: choosing the sales process comes next.
        assert partial["siguiente_paso"] == "proceso_venta"
        sections = {s["clave"]: s["estado"] for s in partial["secciones"]}
        assert sections["situaciones"] == "completa" and sections["multimedia"] == "opcional"
