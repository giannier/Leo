"""Acceptance of the 004 with the real model (TASK-449; run by hand, not in CI).

    RUN_ACCEPTANCE=1 LLM_PROFILE=openai OPENAI_API_KEY=... \
        uv run python -m pytest backend/tests/integration/test_zz_acceptance_live.py -s

What the spec's acceptance criteria ask that the Milestone B walk does not already
cover (scenarios 4 and 5 of Capacita are `test_zz_milestone_b_live.py`):

* **Capacita** scenarios 1 (organic, new), 2 (the path answered by writing),
  3 (arrives from an ad, through the real channel: the simulator has no ads) and
  6 ("lo pensaré", with a closing promotion).
* **Tienda:** an order of 3 products with a promotion, delivery, data, payment,
  receipt and the operator's confirmation.
* **Zentek360:** a quote handed over with a summary, and a fixed-price hosting
  plan sold through to the operator's confirmation.
* In all of them: no file sent twice, no turn above 3 outbound messages.

The report goes to `$ACCEPTANCE_REPORT` (default: the temp directory).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_sandbox_limiter, get_storage_provider
from app.core.config import get_settings
from app.models.business import AgentConfig
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.sales_config import Promotion
from app.process.state import StateRepo
from app.providers.llm.factory import build_provider, platform_profile
from app.providers.storage.mock import MockStorageProvider
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository
from app.services.channel import ConversationResponder
from tests.integration.test_sandbox import NullRateLimiter
from tests.integration.test_zz_milestone_b_live import (
    CAPACITA,
    NO_ORDER,
    ORDERS,
    TENANT,
    Chat,
    Report,
    _business,
    _detail,
    _orders,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ACCEPTANCE") != "1", reason="live walkthrough, run by hand"
)

REPORT = Path(os.getenv("ACCEPTANCE_REPORT") or Path(tempfile.gettempdir()) / "acceptance.md")
D = Decimal
MAX_OUTBOUND = 3


class CountingChat(Chat):
    """The simulator, also checking every turn's outbound count and repeated files."""

    turns_over: list[str] = []
    files: list[str] = []

    async def say(self, text: str) -> dict[str, Any]:
        body = await super().say(text)
        self._count(text, body)
        return body

    async def image(self, name: str) -> dict[str, Any]:
        body = await super().image(name)
        self._count(f"[imagen {name}]", body)
        return body

    def _count(self, said: str, body: dict[str, Any]) -> None:
        sent = body.get("salientes") or []
        if len(sent) > MAX_OUTBOUND:
            self.turns_over.append(f"{said}: {len(sent)} mensajes")
        for call in (body.get("trace") or {}).get("tools_called") or []:
            if (call.get("tool") or call.get("name")) == "enviar_recurso" and call.get(
                "status"
            ) == "hecho":
                self.files.append(f"{self.cid}:{call.get('args_resumen')}")


async def test_acceptance(api: AsyncClient, mail: Any, db: Session, app: Any) -> None:
    storage = MockStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_sandbox_limiter] = NullRateLimiter
    rep = Report()
    rep.lines[0] = "# Aceptación de la 004 — recorrido automático con el modelo real\n"
    CountingChat.turns_over, CountingChat.files = [], []
    try:
        await _capacita(api, mail, db, rep)
        await _tienda(api, mail, db, rep)
        await _zentek(api, mail, db, rep)
        rep.h("En los tres negocios")
        rep.check(
            "Ningún turno supera 3 mensajes salientes",
            not CountingChat.turns_over,
            "; ".join(CountingChat.turns_over),
        )
        rep.check(
            "Ningún archivo se envía dos veces en una conversación",
            len(CountingChat.files) == len(set(CountingChat.files)),
            str(CountingChat.files),
        )
    finally:
        summary = ["\n## Resumen\n"] + [
            f"- {'✅' if ok else '❌'} {name}{f' — {detail}' if detail else ''}"
            for name, ok, detail in rep.checks
        ]
        REPORT.write_text("\n".join(rep.lines + summary) + "\n", encoding="utf-8")  # noqa: ASYNC240


# ------------------------------------------------------------------ Capacita


async def _capacita(api: AsyncClient, mail: Any, db: Session, rep: Report) -> None:
    headers, tid = await _business(api, mail, "Escuela Capacita", "instituto")
    config = db.get(AgentConfig, tid)
    assert config is not None
    config.process_template_key = "certificaciones"
    products = ProductRepository(db, tid)
    for name, price, category, size in CAPACITA:
        products.add(
            name=name,
            price=D(price),
            category=category,
            es_digital=True,
            bundle_size=size,
            bundle_permite_libres=size is not None,
            destacado=size == 5,
        )
    closing = Promotion(
        tenant_id=tid,
        nombre="10 % si te inscribes hoy",
        descripcion="10 % de descuento si confirmas hoy",
        uso="cierre",
        tipo="porcentaje",
        valor=D("10.00"),
        vigencia_horas=24,
    )
    db.add(closing)
    db.commit()
    await api.post(
        f"{TENANT}/payment-methods",
        headers=headers,
        json={"method": "yape", "instructions": "Yape al 987654321 a nombre de Escuela Capacita."},
    )

    rep.h("Capacita 1 — orgánica nueva")
    chat = CountingChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, buenas tardes")
    first_stage = chat.stage()
    await chat.say("Me llamo Ana")
    buttons = [b for s in chat.last.get("salientes") or [] for b in s.get("botones") or []]
    rep.check(
        "Saluda, pide el nombre y con el nombre ofrece los caminos en botones",
        first_stage == "CALIFICAR" and len(buttons) >= 2,
        f"etapa {first_stage}, botones {[b.get('title') for b in buttons]}",
    )

    rep.h("Capacita 2 — calificación respondida escribiendo")
    chat = CountingChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, soy Ana")
    await chat.say("Solo quiero certificar mi experiencia en SIAF")
    rep.check(
        "El camino escrito queda registrado y avanza a IDENTIFICAR",
        chat.facts().get("camino") == "certificacion" and chat.stage() in ("IDENTIFICAR", "PEDIDO"),
        f"camino {chat.facts().get('camino')}, etapa {chat.stage()}",
    )

    rep.h("Capacita 3 — entrada por anuncio (canal real)")
    reply, facts, stage = await _from_an_ad(db, tid)
    rep.lines.append(
        f"- **Cliente:** Hola, vi su anuncio, quiero más información\n  - **Leo:** {reply}"
    )
    rep.check(
        "El anuncio queda en los hechos y la respuesta habla de lo que anunciaba",
        bool(facts.get("entrada_anuncio"))
        and any(w in reply.split(" / ")[-1].lower() for w in ("siaf", "paquete", "500")),
        f"etapa {stage}",
    )

    rep.h("Capacita 6 — «lo pensaré» con promoción de cierre")
    chat = CountingChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, soy Ana. Solo quiero certificar mi experiencia")
    await chat.say("Quiero la certificación individual de SIAF")
    await chat.agree_if_asked()
    await chat.say("Mmm, está un poco caro. Lo voy a pensar")
    offered = str(closing.id) in (chat.facts().get("promos_ofrecidas") or {})
    rep.check(
        "Ofrece la promoción de cierre una vez y la registra",
        offered or "10" in chat.text(),
        f"promos_ofrecidas {chat.facts().get('promos_ofrecidas')}",
    )
    await chat.say("Ok, igual lo pienso")
    rep.check(
        "No la vuelve a ofrecer ni inventa otro descuento",
        "15 %" not in chat.text() and "20 %" not in chat.text(),
        chat.text()[:160],
    )


async def _from_an_ad(db: Session, tid: uuid.UUID) -> tuple[str, dict[str, Any], str]:
    """A customer who tapped a Click-to-WhatsApp ad, through the real channel's responder.

    Capacita v21: the first turn only greets and asks the name, even from an ad; the
    offer comes in the next one. So the customer answers with their name and the
    second reply is the one that must talk about the ad.
    """
    contact = Contact(
        tenant_id=tid,
        phone_e164=f"+5198{uuid.uuid4().int % 10**7:07d}",
        last_inbound_at=datetime.now(UTC),
    )
    db.add(contact)
    db.flush()
    conversation = Conversation(tenant_id=tid, channel="whatsapp", contact_id=contact.id)
    db.add(conversation)
    config = db.get(AgentConfig, tid)
    assert config is not None
    config.is_active = True
    db.flush()
    llm = build_provider(platform_profile(get_settings()))
    payload = {
        "referral": {
            "source_id": "ad-capacita-siaf",
            "headline": "Certifica tu experiencia en SIAF",
            "body": "Paquete de 5 certificaciones a S/ 500",
        }
    }
    replies = []
    for text, extra in (("Hola, vi su anuncio, quiero más información", payload), ("Ana", None)):
        db.add(
            Message(
                tenant_id=tid,
                conversation_id=conversation.id,
                direction="inbound",
                author="customer",
                content=text,
                provider_message_id=f"wamid.{uuid.uuid4().hex}",
                payload=extra,
            )
        )
        db.commit()
        answers = await ConversationResponder(db, tid, llm=llm).respond_turn(conversation.id)
        db.commit()
        replies.append(" ".join(a.content for a in answers))
    state = StateRepo(db, tid).get(conversation.id)
    facts = dict(state.facts) if state else {}
    return " / ".join(replies), facts, state.stage_key if state else ""


# --------------------------------------------------------------------- Tienda


async def _tienda(api: AsyncClient, mail: Any, db: Session, rep: Report) -> None:
    headers, tid = await _business(api, mail, "Tienda Demo", "retail")
    products = ProductRepository(db, tid)
    polo = products.add(name="Polo básico", price=D("35.00"), category="Ropa", destacado=True)
    products.add(name="Gorra urbana", price=D("25.00"), category="Ropa")
    products.add(name="Mochila ejecutiva", price=D("120.00"), category="Accesorios")
    BusinessInfoRepository(db, tid).upsert(
        "entregas",
        {
            "hace_delivery": True,
            "zonas": [
                {"nombre": "Miraflores", "costo": "8.00", "tiempo": "24 h"},
                {"nombre": "Callao", "costo": "12.00", "tiempo": "24 a 48 h"},
            ],
            "envio_gratis_desde": "150.00",
            "recojo_en_tienda": False,
        },
    )
    db.add(
        Promotion(
            tenant_id=tid,
            nombre="2x1 en polos",
            descripcion="Llevas 2 polos y pagas 1",
            uso="automatica",
            tipo="nxm",
            n=2,
            m=1,
            aplica_a="productos",
            product_ids=[polo.id],
        )
    )
    db.commit()
    await api.post(
        f"{TENANT}/payment-methods",
        headers=headers,
        json={"method": "yape", "instructions": "Yape al 912345678 a nombre de Tienda Demo."},
    )

    rep.h("Tienda — 3 productos con promoción, delivery, cobro, comprobante y confirmación")
    chat = CountingChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, quiero 2 polos básicos y una gorra urbana")
    await chat.agree_if_asked()
    order = (await _orders(api, headers) or [NO_ORDER])[0]
    detail = await _detail(api, headers, order["id"]) if order is not NO_ORDER else {}
    units = sum(int(i.get("cantidad", 0)) for i in detail.get("items", []))
    rep.check(
        "Pedido de 3 productos con el 2x1: subtotal 95.00, descuento 35.00, total 60.00",
        units == 3
        and (detail.get("subtotal"), detail.get("descuento"), detail.get("total"))
        == ("95.00", "35.00", "60.00"),
        f"{units} unidades, {detail.get('subtotal')} / {detail.get('descuento')} / "
        f"{detail.get('total')}",
    )
    await chat.say("Soy Ana Quispe, celular 987654321")
    await chat.say("Delivery a Miraflores, Av. Larco 123, frente al parque")
    if "enviar_metodos_pago" not in chat.tools():
        await chat.say("¿Cómo pago?")
    detail = await _detail(api, headers, order["id"]) if order is not NO_ORDER else {}
    rep.check(
        "Delivery a Miraflores: envío 8.00, total 68.00, y le envía los métodos de pago",
        (detail.get("envio"), detail.get("total")) == ("8.00", "68.00")
        and chat.facts().get("metodos_pago_enviados") is not None,
        f"envío {detail.get('envio')}, total {detail.get('total')}",
    )
    await chat.image("comprobante-ok.png")
    order = ((await _orders(api, headers)) or [NO_ORDER])[0]
    confirm = await api.post(
        f"{ORDERS}/{order['id']}/confirm",
        headers=headers,
        json={"siguiente_paso": "Te lo entregamos mañana"},
    )
    await chat.say("Gracias")
    rep.check(
        "El comprobante deja el pedido por confirmar y el operador lo confirma",
        confirm.status_code == 200 and chat.stage() == "CERRADO",
        f"{confirm.status_code}, etapa {chat.stage()}",
    )


# ------------------------------------------------------------------ Zentek360


async def _zentek(api: AsyncClient, mail: Any, db: Session, rep: Report) -> None:
    headers, tid = await _business(api, mail, "Zentek360", "servicios")
    config = db.get(AgentConfig, tid)
    assert config is not None
    config.process_template_key = "servicios"
    products = ProductRepository(db, tid)
    products.add(name="Hosting anual", price=D("350.00"), category="Hosting", es_digital=True)
    products.add(
        name="Desarrollo de página web",
        price=D("0.00"),
        category="Desarrollo",
        es_digital=True,
        modo_venta="cotizacion",
    )
    db.commit()
    await api.post(
        f"{TENANT}/payment-methods",
        headers=headers,
        json={
            "method": "transferencia",
            "instructions": "Transferencia BCP 191-1234567-0-12 a nombre de Zentek360 SAC.",
        },
    )

    rep.h("Zentek360 — cotización derivada con resumen")
    chat = CountingChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, necesito una página web para mi restaurante, con carta y reservas")
    if chat.stage() != "DERIVADO":
        await chat.say("Para el próximo mes, presupuesto unos 2000 soles")
    state = await api.get(f"{TENANT}/conversations/{chat.cid}/state", headers=headers)
    handover = state.json().get("derivado") or {}
    rep.check(
        "Deriva la cotización con un resumen para el asesor",
        chat.stage() == "DERIVADO" and bool(handover.get("resumen")),
        f"motivo {handover.get('motivo')}, resumen {str(handover.get('resumen'))[:120]}",
    )

    rep.h("Zentek360 — hosting de precio fijo vendido")
    chat = CountingChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, quiero contratar el hosting anual")
    await chat.agree_if_asked()
    await chat.say(
        "Soy Carla Díaz, de Restaurante El Sabor, celular 912345678, correo carla@empresa.pe"
    )
    if "enviar_metodos_pago" not in chat.tools():
        await chat.say("¿Cómo pago?")
    await chat.image("comprobante-ok.png")
    order = ((await _orders(api, headers)) or [NO_ORDER])[0]
    confirm = await api.post(
        f"{ORDERS}/{order['id']}/confirm",
        headers=headers,
        json={"siguiente_paso": "Activamos tu hosting hoy"},
    )
    await chat.say("Gracias")
    rep.check(
        "El hosting de S/ 350 llega a cobro, comprobante y confirmación",
        order.get("total") == "350.00" and confirm.status_code == 200 and chat.stage() == "CERRADO",
        f"total {order.get('total')}, {confirm.status_code}, etapa {chat.stage()}",
    )
