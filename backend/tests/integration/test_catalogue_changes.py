"""A product change reaches the agent, and is kept (Giannier, 2026-10-26: «cambié mi producto y no
se actualizó en las respuestas del agente»).

Three gaps: the process configuration (what a quote asks) was cached per process and a product edit
did not clear it; a search showed only the first 200 characters of a description, cut mid-word, so
the second half of an owner's text never reached the model; and in a conversation already going on
the model repeated what it had said before instead of looking again. Nothing recorded what changed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.pipeline import InboundTurn
from app.agent.tools.catalog import MAX_LISTED_DESCRIPTION, buscar_productos, cut_description
from app.agent.tools.registry import ToolContext
from app.models.audit import AuditLog
from app.models.business import Product
from app.models.conversation import Message
from app.process.service import DEFAULT_CACHE, ProcessConfigService
from app.process.turn import CATALOGUE_CHANGED_LINE
from app.providers.llm import ChatResult, MockLLMProvider
from tests.integration.test_products import PRODUCTS, _owner
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _tenant


class TestAnEditIsKeptAndClearsTheCache:
    async def test_an_update_records_what_changed_from_what_to_what(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS, headers=headers, json={"name": "Bidón", "price": "10.00"}
        )
        product_id = created.json()["id"]

        await api.put(
            f"{PRODUCTS}/{product_id}",
            headers=headers,
            json={"price": "12.50", "description": "Ahora con entrega gratis"},
        )

        entries = (
            db.execute(select(AuditLog).where(AuditLog.detail["producto_id"].astext == product_id))
            .scalars()
            .all()
        )
        actions = [entry.action for entry in entries]
        assert actions.count("product.created") == 1 and actions.count("product.updated") == 1
        updated = next(entry for entry in entries if entry.action == "product.updated")
        assert updated.detail["cambios"]["price"] == {"antes": "10.00", "despues": "12.50"}
        assert updated.detail["cambios"]["description"]["despues"] == "Ahora con entrega gratis"

    async def test_an_update_clears_the_cached_configuration(
        self, api: AsyncClient, mail: Any, db: Session
    ) -> None:
        headers = await _owner(api, mail)
        created = await api.post(
            PRODUCTS, headers=headers, json={"name": "Bidón", "price": "10.00"}
        )
        product = db.get(Product, uuid.UUID(created.json()["id"]))
        assert product is not None
        ProcessConfigService(db).effective(product.tenant_id)
        assert DEFAULT_CACHE.get(product.tenant_id) is not None

        await api.put(f"{PRODUCTS}/{product.id}", headers=headers, json={"price": "11.00"})

        assert DEFAULT_CACHE.get(product.tenant_id) is None

    async def test_a_removal_is_recorded(self, api: AsyncClient, mail: Any, db: Session) -> None:
        headers = await _owner(api, mail)
        created = await api.post(PRODUCTS, headers=headers, json={"name": "Caja", "price": "9.00"})
        product_id = created.json()["id"]

        await api.delete(f"{PRODUCTS}/{product_id}", headers=headers)

        actions = (
            db.execute(
                select(AuditLog.action).where(AuditLog.detail["producto_id"].astext == product_id)
            )
            .scalars()
            .all()
        )
        assert "product.deleted" in actions


class TestTheWholeDescriptionReachesTheAgent:
    async def test_a_long_description_of_a_short_listing_is_shown_whole(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        tail = "Dime cuántas unidades necesitas y dónde deseas recibirlas."
        description = ("Agua de 20 litros para hogares, oficinas y negocios. " * 6) + tail
        db.add(Product(tenant_id=tenant.id, name="Bidón 20L", price=10, description=description))
        db.flush()
        context = ToolContext(session=db, tenant_id=tenant.id, llm=MockLLMProvider())

        result = await buscar_productos(context, {"query": "Bidón"})

        assert len(description) > 200 and result.texto is not None and tail in result.texto

    def test_what_does_not_fit_is_cut_at_a_sentence_or_a_word(self) -> None:
        text = "Primera frase completa. " * 40

        cut = cut_description(text, MAX_LISTED_DESCRIPTION)

        assert len(cut) <= MAX_LISTED_DESCRIPTION + 2
        assert cut.endswith("…") and "completa. …" in cut


class TestAConversationGoingOnIsToldTheCatalogueChanged:
    async def test_after_an_edit_the_model_is_told_to_look_again(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        conversation = _conversation(db, tenant)
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="El bidón cuesta S/ 10.",
                created_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        db.add(Product(tenant_id=tenant.id, name="Bidón 20L", price=12))
        db.flush()
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="Hoy el bidón cuesta S/ 12."))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="¿y cuánto era el bidón?")
        )

        assert CATALOGUE_CHANGED_LINE in llm.chat_calls[0].system

    async def test_without_a_change_nothing_is_said(self, db: Session) -> None:
        tenant = _tenant(db, template="productos")
        db.add(Product(tenant_id=tenant.id, name="Bidón 20L", price=12))
        db.flush()
        conversation = _conversation(db, tenant)
        db.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                author="agent",
                content="Hola, ¿en qué te ayudo?",
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
        db.flush()
        llm = MockLLMProvider()
        llm.queue(ChatResult(content="El bidón cuesta S/ 12."))

        await _pipeline(db, tenant, conversation, llm).run(
            conversation.id, InboundTurn(text="¿cuánto cuesta el bidón?")
        )

        assert CATALOGUE_CHANGED_LINE not in llm.chat_calls[0].system
