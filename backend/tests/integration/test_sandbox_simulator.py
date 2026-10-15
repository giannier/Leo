"""The simulator as a simulator: typed outbounds, debug panel, reset (TASK-422, C-19).

`test_sandbox.py` covers the endpoints as they were; this covers what Feature 004
adds - the customer taps a button, the agent sends a file, the owner reads the
stage in the debug panel, and "Reiniciar conversación" starts over. The model is a
script, so these assert what the server does with what a model says.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_platform_llm, get_sandbox_limiter, get_storage_provider
from app.core.rate_limit import NullRateLimiter
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order
from app.process.state import StateRepo
from app.providers.llm import ChatResult, MockLLMProvider
from app.providers.storage.base import StorageError
from app.providers.storage.mock import MockStorageProvider
from app.repositories.conversation import MessageRepository
from app.repositories.media import MediaAssetRepository
from app.repositories.orders import OrderRepository
from tests.integration.test_sandbox import SANDBOX, TENANT, _ready_owner, _tool_call


@pytest.fixture
def llm(app: Any) -> MockLLMProvider:
    """A scripted model, so these test the endpoints and not a vendor."""
    provider = MockLLMProvider()
    app.dependency_overrides[get_platform_llm] = lambda: provider
    app.dependency_overrides[get_sandbox_limiter] = NullRateLimiter
    return provider


async def _certificaciones_conversation(
    api: AsyncClient, mail: Any, db: Session
) -> tuple[dict[str, str], str, uuid.UUID]:
    """An owner whose business runs the `certificaciones` process (paths to tap)."""
    headers = await _ready_owner(api, mail)
    tenant_id = uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"])
    config = db.get(AgentConfig, tenant_id)
    assert config is not None
    config.process_template_key = "certificaciones"
    db.flush()
    conversation_id = (await api.post(f"{SANDBOX}/conversations", headers=headers)).json()[
        "conversation_id"
    ]
    return headers, conversation_id, tenant_id


def _messages_url(conversation_id: str, query: str = "") -> str:
    return f"{SANDBOX}/conversations/{conversation_id}/messages{query}"


class TestTypedOutbounds:
    async def test_tapping_a_button_moves_the_stage_and_the_debug_panel_says_so(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        """TASK-422's verification, on the server side: the whole path from the
        greeting to a tapped button, with the debug panel reporting each stage."""
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)

        llm.queue(ChatResult(content="¡Hola! Soy Leo. ¿Cómo te llamas?"))
        greeting = await api.post(
            _messages_url(conversation_id, "?debug=true"),
            headers=headers,
            json={"content": "hola"},
        )
        assert greeting.status_code == 200, greeting.text
        debug = greeting.json()["debug"]
        assert (debug["etapa_antes"], debug["etapa_despues"]) == ("INICIO", "CALIFICAR")
        assert debug["embudo"] == "NUEVO"
        assert debug["hechos"]["saludo_enviado"] is True
        assert "enviar_recurso" in debug["herramientas_habilitadas"]
        assert [s["tipo"] for s in greeting.json()["salientes"]] == ["texto"]

        llm.queue(
            ChatResult(
                content="",
                tool_calls=(
                    _tool_call("mostrar_opciones", tipo="caminos", texto="¿Qué necesitas?"),
                ),
            )
        )
        llm.queue(ChatResult(content=""))
        options = await api.post(
            _messages_url(conversation_id, "?debug=true"),
            headers=headers,
            json={"content": "quiero información"},
        )
        body = options.json()
        assert [s["tipo"] for s in body["salientes"]] == ["botones"]
        buttons = body["salientes"][0]["botones"]
        assert [b["id"] for b in buttons] == ["camino:certificacion", "camino:programa"]
        assert body["reply"] is None
        assert body["debug"]["herramientas_llamadas"] == [
            {"nombre": "mostrar_opciones", "status": "hecho"}
        ]
        assert body["debug"]["etapa_despues"] == "CALIFICAR"

        llm.queue(ChatResult(content="Perfecto. ¿Qué tema quieres certificar?"))
        tapped = await api.post(
            _messages_url(conversation_id, "?debug=true"),
            headers=headers,
            json={"interactive_id": buttons[0]["id"], "title": buttons[0]["title"]},
        )
        debug = tapped.json()["debug"]
        assert (debug["etapa_antes"], debug["etapa_despues"]) == ("CALIFICAR", "IDENTIFICAR")
        assert debug["hechos"]["camino"] == "certificacion"
        assert "elegir_camino" in debug["herramientas_habilitadas"]
        # The model never had to call `elegir_camino` for the path to be recorded.
        assert tapped.json()["trace"]["tools_called"] == []

    async def test_the_debug_panel_is_only_sent_when_asked_for(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)
        llm.queue(ChatResult(content="¡Hola!"))

        response = await api.post(
            _messages_url(conversation_id), headers=headers, json={"content": "hola"}
        )

        assert response.json()["debug"] is None

    async def test_a_tap_needs_its_title_and_a_message_needs_one_kind(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)

        for body in (
            {"interactive_id": "camino:certificacion"},
            {"content": "hola", "interactive_id": "camino:certificacion", "title": "x"},
            {},
        ):
            response = await api.post(_messages_url(conversation_id), headers=headers, json=body)
            assert response.status_code == 400, body
            assert response.json()["error"] == "validation_error"

    async def test_a_library_file_arrives_as_a_typed_message_with_a_link(
        self, api: AsyncClient, mail: Any, app: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)
        storage = MockStorageProvider()
        storage.put("media/saludo.png", b"png", content_type="image/png")
        app.dependency_overrides[get_storage_provider] = lambda: storage
        MediaAssetRepository(db, tenant_id).add(
            clave="saludo",
            tipo="imagen",
            titulo="Saludo",
            cuando_usar="Siempre al saludar",
            caption="Bienvenido a Capacita",
            storage_key="media/saludo.png",
            filename="saludo.png",
            mime_type="image/png",
            size_bytes=10,
        )
        llm.queue(
            ChatResult(content="", tool_calls=(_tool_call("enviar_recurso", clave="saludo"),))
        )
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))

        response = await api.post(
            _messages_url(conversation_id), headers=headers, json={"content": "hola"}
        )

        first, second = response.json()["salientes"]
        assert (first["tipo"], first["clave"], first["caption"]) == (
            "imagen",
            "saludo",
            "Bienvenido a Capacita",
        )
        assert "saludo.png" in first["url"]
        assert (second["tipo"], second["texto"]) == ("texto", "¡Hola! ¿Cómo te llamas?")
        # The transcript keeps the typed form, so a reload draws the same thing.
        history = (await api.get(_messages_url(conversation_id), headers=headers)).json()
        typed = [m["saliente"]["tipo"] for m in history["items"] if m["saliente"]]
        assert typed == ["imagen", "texto"]

    async def test_a_file_that_left_the_store_is_still_shown_without_a_link(
        self, api: AsyncClient, mail: Any, app: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)
        app.dependency_overrides[get_storage_provider] = lambda: MockStorageProvider()
        MediaAssetRepository(db, tenant_id).add(
            clave="malla",
            tipo="documento",
            titulo="Malla",
            cuando_usar="Cuando pidan la malla",
            storage_key="media/gone.pdf",
            filename="malla.pdf",
            mime_type="application/pdf",
            size_bytes=10,
        )
        llm.queue(ChatResult(content="", tool_calls=(_tool_call("enviar_recurso", clave="malla"),)))
        llm.queue(ChatResult(content="Te envié la malla."))

        response = await api.post(
            _messages_url(conversation_id), headers=headers, json={"content": "malla"}
        )

        first = response.json()["salientes"][0]
        assert (first["tipo"], first["clave"], first["url"]) == ("documento", "malla", None)

    async def test_an_image_is_accepted_and_the_model_is_told_one_arrived(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session, app: Any
    ) -> None:
        storage = MockStorageProvider()
        app.dependency_overrides[get_storage_provider] = lambda: storage
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)
        llm.queue(ChatResult(content="Recibí tu imagen, ¿en qué te ayudo?"))

        response = await api.post(
            _messages_url(conversation_id),
            headers=headers,
            files={"image": ("voucher.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")},
        )

        assert response.status_code == 200, response.text
        assert "[El cliente envió una imagen]" in llm.chat_calls[0].messages[0].content
        # The photo is kept in private storage for the turn that expects a receipt.
        assert len(storage.objects) == 1

    async def test_a_storage_failure_does_not_take_the_turn_down(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session, app: Any
    ) -> None:
        """Giannier's first test: a fresh MinIO without its bucket answered the
        image with a 500, which the panel reads as "No pudimos contactar"."""

        class Broken(MockStorageProvider):
            def put(self, key: str, data: bytes, *, content_type: str) -> str:
                raise StorageError("no bucket")

        app.dependency_overrides[get_storage_provider] = lambda: Broken()
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)
        llm.queue(ChatResult(content="Recibí tu imagen."))

        response = await api.post(
            _messages_url(conversation_id),
            headers=headers,
            files={"image": ("voucher.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["reply"]["content"] == "Recibí tu imagen."

    @pytest.mark.parametrize(
        ("name", "data", "mime"),
        [
            ("notes.txt", b"hello", "text/plain"),
            ("empty.png", b"", "image/png"),
            ("huge.png", b"0" * (5 * 1024 * 1024 + 1), "image/png"),
        ],
    )
    async def test_a_bad_image_is_rejected(
        self,
        api: AsyncClient,
        mail: Any,
        llm: MockLLMProvider,
        db: Session,
        name: str,
        data: bytes,
        mime: str,
    ) -> None:
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)

        response = await api.post(
            _messages_url(conversation_id), headers=headers, files={"image": (name, data, mime)}
        )

        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    async def test_a_multipart_body_without_an_image_is_rejected(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)

        response = await api.post(
            _messages_url(conversation_id),
            headers=headers,
            data={"content": "hola"},
            files={"other": ("x.txt", b"x", "text/plain")},
        )

        assert response.status_code == 400

    async def test_the_simulators_customer_is_a_synthetic_contact(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        _, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)

        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        assert conversation is not None and conversation.contact_id is not None
        contact = db.get(Contact, conversation.contact_id)
        assert contact is not None
        assert contact.tenant_id == tenant_id
        assert contact.phone_e164 is None
        assert contact.external_user_id == f"sandbox:{conversation.started_by}"

    async def test_a_conversation_without_a_contact_gets_one_on_its_first_message(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        """Conversations opened before the simulator had a contact keep working."""
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        assert conversation is not None
        conversation.contact_id = None
        db.flush()
        llm.queue(ChatResult(content="¡Hola!"))

        response = await api.post(
            _messages_url(conversation_id), headers=headers, json={"content": "hola"}
        )

        assert response.status_code == 200
        db.refresh(conversation)
        assert conversation.contact_id is not None


class TestAsynchronousOutbounds:
    async def test_after_returns_only_what_the_agent_said_later(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)
        llm.queue(ChatResult(content="¡Hola!"))
        sent = await api.post(
            _messages_url(conversation_id), headers=headers, json={"content": "hola"}
        )
        anchor = sent.json()["reply"]["message_id"]
        # Something the system wrote later, with no customer message behind it.
        MessageRepository(db, tenant_id).append(
            conversation_id=uuid.UUID(conversation_id),
            direction="outbound",
            author="system",
            content="Tu pago fue confirmado.",
        )

        polled = await api.get(_messages_url(conversation_id, f"?after={anchor}"), headers=headers)

        items = polled.json()["items"]
        assert [i["content"] for i in items] == ["Tu pago fue confirmado."]
        assert items[0]["saliente"]["tipo"] == "texto"
        assert polled.json()["total"] == 1

        nothing = await api.get(
            _messages_url(conversation_id, f"?after={items[0]['id']}"), headers=headers
        )
        assert nothing.json()["items"] == []

    async def test_an_unknown_anchor_reads_as_no_anchor(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, _ = await _certificaciones_conversation(api, mail, db)
        llm.queue(ChatResult(content="¡Hola!"))
        await api.post(_messages_url(conversation_id), headers=headers, json={"content": "hola"})

        polled = await api.get(
            _messages_url(conversation_id, f"?after={uuid.uuid4()}"), headers=headers
        )

        assert polled.status_code == 200
        assert len(polled.json()["items"]) == 1


class TestReset:
    async def test_reset_starts_the_conversation_over(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)
        llm.queue(ChatResult(content="¡Hola! ¿Cómo te llamas?"))
        await api.post(_messages_url(conversation_id), headers=headers, json={"content": "hola"})
        assert StateRepo(db, tenant_id).get(uuid.UUID(conversation_id)) is not None

        reset = await api.post(f"{SANDBOX}/conversations/{conversation_id}/reset", headers=headers)

        assert reset.status_code == 204
        history = (await api.get(_messages_url(conversation_id), headers=headers)).json()
        assert history["total"] == 0
        assert StateRepo(db, tenant_id).get(uuid.UUID(conversation_id)) is None
        # The greeting is due again: the first turn after a reset is INICIO.
        llm.queue(ChatResult(content="¡Hola de nuevo! ¿Cómo te llamas?"))
        again = await api.post(
            _messages_url(conversation_id, "?debug=true"),
            headers=headers,
            json={"content": "hola"},
        )
        assert again.json()["debug"]["etapa_antes"] == "INICIO"
        assert again.json()["debug"]["hechos"]["saludo_enviado"] is True

    async def test_reset_removes_the_sandbox_order_and_nothing_else(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        assert conversation is not None and conversation.contact_id is not None
        db.add(
            Order(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                contact_id=conversation.contact_id,
                numero=1,
                moneda="PEN",
                is_sandbox=True,
            )
        )
        db.flush()

        await api.post(f"{SANDBOX}/conversations/{conversation_id}/reset", headers=headers)

        assert list(OrderRepository(db, tenant_id).for_conversation(conversation.id)) == []
        # The conversation itself and its contact stay: the panel keeps its id.
        assert db.get(Conversation, conversation.id) is not None
        assert db.get(Contact, conversation.contact_id) is not None

    async def test_an_unknown_conversation_cannot_be_reset(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        headers, _, _ = await _certificaciones_conversation(api, mail, db)

        response = await api.post(f"{SANDBOX}/conversations/{uuid.uuid4()}/reset", headers=headers)

        assert response.status_code == 404


class TestSituationDrafts:
    """RF-009: a draft is tried in the simulator only when asked, and the debug
    panel says which situations entered the turn."""

    async def test_a_draft_enters_only_when_tried(
        self, api: AsyncClient, mail: Any, llm: MockLLMProvider, db: Session
    ) -> None:
        from app.services.situations import SituationInput, SituationService, embed_pending

        headers, conversation_id, tenant_id = await _certificaciones_conversation(api, mail, db)
        when = "El cliente pide factura o boleta con RUC"
        draft = SituationService(db, uuid.UUID(str(tenant_id))).create(
            SituationInput(titulo="Factura", cuando_aplica=when, que_hace="Pide el RUC."),
            by=None,
        )
        await embed_pending(db, uuid.UUID(str(tenant_id)), llm)
        db.flush()

        llm.queue(ChatResult(content="Claro."))
        plain = await api.post(
            _messages_url(conversation_id, "?debug=true"), headers=headers, json={"content": when}
        )
        llm.queue(ChatResult(content="Claro, ¿tu RUC?"))
        tried = await api.post(
            _messages_url(conversation_id, "?debug=true"),
            headers=headers,
            json={"content": when, "situaciones_prueba": [str(draft.id)]},
        )

        assert plain.json()["debug"]["situaciones"] == []
        entered = tried.json()["debug"]["situaciones"]
        assert [(s["titulo"], s["borrador"]) for s in entered] == [("Factura", True)]
        assert entered[0]["distancia"] is not None
        assert "SITUACIONES DEL NEGOCIO" in llm.chat_calls[-1].messages[0].content
        assert "«Factura»" in llm.chat_calls[-1].messages[0].content
        assert tried.json()["debug"]["reglas_apagadas"] == {}
