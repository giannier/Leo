"""The universal sales process (feature 020, phase F2), end to end with the model mocked.

It is a fourth template beside the three it replaces, hidden from businesses until it passes the
real-model evals: the platform moves a business to it, audited, and back. No OpenAI call here
(Giannier, 2026-10-28: no API spending until the budget is approved).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.agent.tools.definitions import registry_for_stage
from app.agent.tools.registry import ToolContext
from app.models.audit import AuditLog
from app.models.business import AgentConfig, Product
from app.process.facts import Facts
from app.process.layers import LayerResolver
from app.process.rule_split import join_rules
from app.process.service import ProcessConfigCache, ProcessConfigService
from app.process.templates import TEMPLATES, UNIVERSAL
from app.process.templates.base import Stage
from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_platform_admin import _admin, _register
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _tenant

PLATFORM = "/api/v1/platform-admin"
ASSISTANT = "/api/v1/assistant"


def _catalogue(db: Session, tenant_id: uuid.UUID) -> None:
    """One item of each closing: an order, a quote, an appointment and an advisor."""
    db.add_all(
        [
            Product(tenant_id=tenant_id, name="Polo", price=Decimal("35")),
            Product(
                tenant_id=tenant_id, name="Uniformes por mayor", price=Decimal("0"),
                modo_venta="cotizacion",
            ),
            Product(
                tenant_id=tenant_id, name="Asesoría de imagen", price=Decimal("80"),
                tipo="servicio", agendable=True, duracion_min=60,
            ),
            Product(
                tenant_id=tenant_id, name="Colección a medida", price=Decimal("0"),
                tipo="servicio", modo_venta="informar",
            ),
        ]
    )  # fmt: skip
    db.flush()


class TestTheVersionInTheCatalogue:
    def test_version_one_is_published_and_reads_back_as_the_code_text(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        rules = LayerResolver(db, tenant.id).resolve(UNIVERSAL, None)

        assert rules.process_version == 1
        template = TEMPLATES[UNIVERSAL]
        for stage in Stage:
            assert join_rules(rules.process_by_stage[stage.value]) == (
                template.stage(stage).instrucciones
            ), stage

    def test_its_rules_do_not_share_switches_with_the_old_templates(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)

        keys = [rule.rule_key for rule in LayerResolver(db, tenant.id).rules("proceso", UNIVERSAL)]

        assert keys and all(key.startswith("universal.") for key in keys)


class TestHowTheCatalogueCloses:
    def test_the_universal_process_knows_the_closings_its_items_use(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        _catalogue(db, tenant.id)

        config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)

        assert config.cierres == ("pedido", "cotizacion", "cita", "informar")

    def test_an_old_template_does_not_load_them(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        _catalogue(db, tenant.id)

        config = ProcessConfigService(db, cache=ProcessConfigCache()).effective(tenant.id)

        assert config.cierres == ()


class TestWhatTheSearchShows:
    async def _search(self, db: Session, template_key: str) -> str:
        tenant = _tenant(db, template=template_key)
        _catalogue(db, tenant.id)
        conversation = _conversation(db, tenant)
        template = TEMPLATES[template_key]
        options = template.resolve()
        registry = registry_for_stage(template, Stage.IDENTIFICAR, Facts(), options)
        tool = registry.get("buscar_productos")
        assert tool is not None
        context = ToolContext(
            session=db,
            tenant_id=tenant.id,
            llm=MockLLMProvider(),
            conversation_id=conversation.id,
            options=options,
            sandbox=True,
        )
        result: Any = await tool.handler(context, {"consulta": "asesoría"})
        return str(result.texto)

    async def test_a_bookable_item_says_so_in_the_universal_process(self, db: Session) -> None:
        assert "[se agenda]" in await self._search(db, UNIVERSAL)

    async def test_the_old_templates_read_the_catalogue_as_before(self, db: Session) -> None:
        assert "[se agenda]" not in await self._search(db, "productos")


class TestATurn:
    async def test_the_prompt_says_how_each_item_closes(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        _catalogue(db, tenant.id)
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! Soy Leo. ¿En qué te ayudo?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        prompt = llm.chat_calls[0].system
        assert "CÓMO SE CIERRA" in prompt
        assert "Lo arma una persona" in prompt
        assert "Es tu primer mensaje en esta conversación." in prompt

    async def test_a_shop_that_only_sells_reads_only_the_order_line(self, db: Session) -> None:
        tenant = _tenant(db, template=UNIVERSAL)
        db.add(Product(tenant_id=tenant.id, name="Polo", price=Decimal("35")))
        db.flush()
        conversation = _conversation(db, tenant)
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="¡Hola! ¿Qué buscas?"))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="hola")
        )

        prompt = llm.chat_calls[0].system
        assert "- Precio fijo:" in prompt
        assert "A cotizar" not in prompt and "Se agenda" not in prompt


class TestTheSwitch:
    async def test_the_platform_moves_a_business_and_back_audited(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        admin, admin_id = await _admin(api, mail, db)
        owner, owner_id = await _register(api, mail, db)
        tenant_id = _tenant_of(db, owner_id)

        moved = await api.put(
            f"{PLATFORM}/tenants/{tenant_id}/process", headers=admin, json={"universal": True}
        )
        assert moved.status_code == 200 and moved.json()["template_key"] == UNIVERSAL
        agent = db.execute(
            select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)
        ).scalar_one()
        db.refresh(agent)
        assert agent.process_template_key == UNIVERSAL and agent.plantilla_manual is True

        back = await api.put(
            f"{PLATFORM}/tenants/{tenant_id}/process", headers=admin, json={"universal": False}
        )
        assert back.status_code == 200 and back.json()["template_key"] != UNIVERSAL
        actions = {
            row.action
            for row in db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id)).scalars()
        }
        assert {"process.universal", "process.templates"} <= actions
        assert admin_id != owner_id

    async def test_a_business_cannot_switch_itself(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        owner, owner_id = await _register(api, mail, db)
        tenant_id = _tenant_of(db, owner_id)

        refused = await api.put(
            f"{PLATFORM}/tenants/{tenant_id}/process", headers=owner, json={"universal": True}
        )
        chosen = await api.put(
            f"{ASSISTANT}/process", headers=owner, json={"template_key": UNIVERSAL}
        )

        # 404-shaped, like every platform route for an ordinary account (`get_platform_admin`).
        assert refused.status_code == 404
        assert chosen.status_code == 422 and chosen.json()["error"] == "unknown_template"

    async def test_only_the_business_on_it_sees_it_and_may_save_its_options(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        admin, _ = await _admin(api, mail, db)
        owner, owner_id = await _register(api, mail, db)
        other, _ = await _register(api, mail, db)
        tenant_id = _tenant_of(db, owner_id)
        await api.put(
            f"{PLATFORM}/tenants/{tenant_id}/process", headers=admin, json={"universal": True}
        )

        mine = (await api.get(f"{ASSISTANT}/templates", headers=owner)).json()["items"]
        theirs = (await api.get(f"{ASSISTANT}/templates", headers=other)).json()["items"]
        saved = await api.put(
            f"{ASSISTANT}/process",
            headers=owner,
            json={"template_key": UNIVERSAL, "options": {"modalidad_pago": "contra_entrega"}},
        )

        assert UNIVERSAL in {t["key"] for t in mine}
        assert UNIVERSAL not in {t["key"] for t in theirs}
        assert saved.status_code == 200 and saved.json()["template_key"] == UNIVERSAL

    async def test_an_unknown_business_is_not_found(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        admin, _ = await _admin(api, mail, db)

        missing = await api.put(
            f"{PLATFORM}/tenants/{uuid.uuid4()}/process", headers=admin, json={"universal": True}
        )

        assert missing.status_code == 404


def _tenant_of(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    from app.models.tenant import TenantUser

    return db.execute(
        select(TenantUser.tenant_id).where(TenantUser.user_id == user_id)
    ).scalar_one()
