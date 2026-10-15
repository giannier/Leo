"""Milestone B walked through the API with the real model (run by hand, not in CI).

    RUN_MILESTONE_B=1 LLM_PROFILE=openai OPENAI_API_KEY=... \
        uv run python -m pytest backend/tests/integration/test_zz_milestone_b_live.py -s

Every scenario of `milestones/B.md` sends the same messages as the guide through
the simulator's API and the operator's actions through the Pedidos API. It
writes a transcript and the checklist results to `MILESTONE_B_REPORT` (by default
`milestone_b_report.md` in the temp directory). The receipt images are synthetic
(`tests/fixtures/milestone_b/`): S/ 500 to Escuela Capacita, S/ 450, and a photo
that is not a receipt.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_sandbox_limiter, get_storage_provider
from app.models.business import AgentConfig
from app.models.sales_config import Promotion
from app.process.service import DEFAULT_CACHE
from app.providers.storage.mock import MockStorageProvider
from app.repositories.product import ProductRepository
from app.repositories.sales_config import BusinessInfoRepository
from tests.integration.test_sandbox import AUTH, NullRateLimiter, _link_token

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MILESTONE_B") != "1", reason="live walkthrough, run by hand"
)

TENANT = "/api/v1/tenant"
SANDBOX = f"{TENANT}/sandbox"
ORDERS = f"{TENANT}/orders"
IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "milestone_b"
REPORT = Path(
    os.getenv("MILESTONE_B_REPORT") or Path(tempfile.gettempdir()) / "milestone_b_report.md"
)
D = Decimal

CAPACITA = [
    ("Certificación individual SIAF", "120.00", "Sector Público", None),
    ("Certificación individual Gestión pública", "120.00", "Sector Público", None),
    ("Certificación individual Contrataciones con el Estado", "120.00", "Contrataciones", None),
    ("Certificación individual SEACE", "120.00", "Contrataciones", None),
    ("Certificación individual Excel avanzado", "120.00", "Ofimática", None),
    ("Certificación individual Power BI", "120.00", "Ofimática", None),
    ("Certificación individual Gestión de proyectos", "120.00", "Gestión", None),
    ("Certificación individual Derecho administrativo", "120.00", "Derecho", None),
    ("Paquete 2 certificaciones", "200.00", "Paquetes", 2),
    ("Paquete 5 certificaciones", "500.00", "Paquetes", 5),
    ("Paquete 10 certificaciones", "900.00", "Paquetes", 10),
]


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = ["# Milestone B — recorrido automático con el modelo real\n"]
        self.checks: list[tuple[str, bool, str]] = []

    def h(self, title: str) -> None:
        self.lines.append(f"\n## {title}\n")

    def turn(self, said: str, body: dict[str, Any]) -> None:
        dbg = body.get("debug") or {}
        calls = (body.get("trace") or {}).get("tools_called") or []
        tools = "; ".join(
            f"{c.get('tool') or c.get('name')}({c.get('args_resumen', '')})→{c.get('status', '')}"
            for c in calls
        )
        pedido = (dbg.get("hechos") or {}).get("pedido")
        total = f" · pedido {pedido['estado']} {pedido['total']}" if pedido else ""
        self.lines.append(
            f"- **Cliente:** {said}\n"
            f"  - etapa {dbg.get('etapa_antes')} → {dbg.get('etapa_despues')}"
            f" · embudo {dbg.get('embudo')} · tools [{tools}]{total}"
        )
        for s in body.get("salientes") or []:
            text = s.get("texto") or s.get("body") or s.get("caption") or s.get("titulo") or ""
            extra = ""
            if s.get("botones"):
                extra = " · botones: " + ", ".join(b["title"] for b in s["botones"])
            self.lines.append(f"  - **Leo** ({s['tipo']}): {text.strip()}{extra}")

    def note(self, text: str) -> None:
        self.lines.append(f"- {text}")

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))
        self.lines.append(f"- {'✅' if ok else '❌'} {name}{f' — {detail}' if detail else ''}")

    def write(self) -> None:
        summary = ["\n## Resumen del checklist\n"]
        for name, ok, detail in self.checks:
            summary.append(f"- {'✅' if ok else '❌'} {name}{f' — {detail}' if detail else ''}")
        REPORT.write_text("\n".join(self.lines + summary) + "\n", encoding="utf-8")


class Chat:
    def __init__(self, api: AsyncClient, headers: dict[str, str], report: Report) -> None:
        self.api, self.headers, self.report = api, headers, report
        self.cid = ""
        self.last: dict[str, Any] = {}

    async def open(self) -> None:
        r = await self.api.post(f"{SANDBOX}/conversations", headers=self.headers)
        assert r.status_code in (200, 201), r.text
        self.cid = r.json()["conversation_id"]

    async def reset(self) -> None:
        await self.api.post(f"{SANDBOX}/conversations/{self.cid}/reset", headers=self.headers)

    async def say(self, text: str) -> dict[str, Any]:
        r = await self.api.post(
            f"{SANDBOX}/conversations/{self.cid}/messages?debug=true",
            headers=self.headers,
            json={"content": text},
        )
        assert r.status_code == 200, r.text
        self.last = r.json()
        self.report.turn(text, self.last)
        return self.last

    async def tap(self, needle: str) -> dict[str, Any] | None:
        for s in reversed(self.last.get("salientes") or []):
            options = list(s.get("botones") or [])
            for section in s.get("filas") or []:
                options.extend(section.get("rows") or section.get("filas") or [section])
            for o in options:
                if needle.lower() in str(o.get("title", "")).lower():
                    r = await self.api.post(
                        f"{SANDBOX}/conversations/{self.cid}/messages?debug=true",
                        headers=self.headers,
                        json={"interactive_id": o["id"], "title": o["title"]},
                    )
                    assert r.status_code == 200, r.text
                    self.last = r.json()
                    self.report.turn(f"[toca «{o['title']}»]", self.last)
                    return self.last
        self.report.note(f"(no apareció la opción «{needle}»; se escribe)")
        return None

    async def image(self, name: str) -> dict[str, Any]:
        data = (IMAGES / name).read_bytes()
        r = await self.api.post(
            f"{SANDBOX}/conversations/{self.cid}/messages?debug=true",
            headers=self.headers,
            files={"image": (name, data, "image/png")},
        )
        assert r.status_code == 200, r.text
        self.last = r.json()
        self.report.turn(f"[imagen {name}]", self.last)
        return self.last

    def facts(self) -> dict[str, Any]:
        return (self.last.get("debug") or {}).get("hechos") or {}

    def stage(self) -> str:
        return str((self.last.get("debug") or {}).get("etapa_despues"))

    def tools(self) -> list[str]:
        calls = (self.last.get("debug") or {}).get("herramientas_llamadas") or []
        return [str(t.get("nombre")) for t in calls]

    async def agree_if_asked(self) -> None:
        """A customer answers "sí" when the agent asks before adding (noted in the report)."""
        if not self.facts().get("pedido") and "?" in self.text():
            self.report.note("(el agente preguntó antes de agregar; el cliente responde «sí»)")
            await self.say("Sí")

    def text(self) -> str:
        return " ".join(
            str(s.get("texto") or s.get("body") or "") for s in self.last.get("salientes") or []
        )


async def _business(
    api: AsyncClient, mail: Any, name: str, industry: str
) -> tuple[dict[str, str], uuid.UUID]:
    body = {
        "full_name": "Giannier Prueba",
        "email": f"b-{uuid.uuid4().hex[:10]}@example.com",
        "password": "una-clave-suficientemente-larga",
        "business_name": name,
    }
    assert (await api.post(f"{AUTH}/register", json=body)).status_code == 201
    await api.post(f"{AUTH}/verify-email", json={"token": _link_token(mail.last)})
    login = await api.post(
        f"{AUTH}/login", json={"email": body["email"], "password": body["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    await api.put(
        f"{TENANT}/business-profile",
        headers=headers,
        json={"business_name": name, "industry": industry, "description": f"{name}, Lima."},
    )
    await api.put(
        f"{TENANT}/agent-config",
        headers=headers,
        json={"agent_name": "Leo", "tone": "cercano", "objective": "vender"},
    )
    tid = uuid.UUID((await api.get(TENANT, headers=headers)).json()["id"])
    return headers, tid


NO_ORDER: dict[str, Any] = {"id": "ninguno", "estado": None, "observaciones": []}


async def _orders(api: AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    r = await api.get(f"{ORDERS}?sandbox=true", headers=headers)
    return list(r.json()["items"])


async def _detail(api: AsyncClient, headers: dict[str, str], oid: str) -> dict[str, Any]:
    return dict((await api.get(f"{ORDERS}/{oid}", headers=headers)).json())


async def test_milestone_b(api: AsyncClient, mail: Any, db: Session, app: Any) -> None:
    storage = MockStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_sandbox_limiter] = NullRateLimiter
    rep = Report()
    try:
        await _capacita(api, mail, db, rep)
        await _tienda(api, mail, db, rep)
    finally:
        rep.write()


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
    db.commit()
    await api.post(
        f"{TENANT}/payment-methods",
        headers=headers,
        json={
            "method": "yape",
            "instructions": "Yape al 987654321 a nombre de Escuela Capacita. "
            "Cuando pagues, envía la foto del comprobante por aquí.",
        },
    )
    chat = Chat(api, headers, rep)
    await chat.open()

    rep.h("Escenario 4 — Capacita, paquete de 5 con 2 temas")
    await chat.say("Hola")
    await chat.say("Me llamo Ana")
    if await chat.tap("Solo certificar") is None:
        await chat.say("Solo quiero certificarme")
    await chat.say("Necesito certificarme en SIAF")
    tapped = await chat.tap("Paquete 5")
    if tapped is None:
        await chat.say("Quiero el paquete de 5 certificaciones, con SIAF y Excel avanzado")
    else:
        await chat.say("Quiero ese, con SIAF y Excel avanzado")
    pedido = chat.facts().get("pedido") or {}
    rep.check(
        "E4.1 paquete de 5 con 2 temas queda en PEDIDO con faltan 3",
        pedido.get("faltan_selecciones") == 3 and chat.stage() == "PEDIDO",
        f"faltan {pedido.get('faltan_selecciones')}, etapa {chat.stage()}",
    )
    await chat.say("SEACE, Power BI y Gestión de museos")
    pedido = chat.facts().get("pedido") or {}
    names = [i["selecciones"] for i in pedido.get("items") or []]
    rep.check(
        "E4.2 con 5 temas el pedido está completo y pasa a DATOS; el tema libre se acepta",
        bool(pedido.get("completo"))
        and chat.stage() in ("DATOS", "COBRO")
        and any("museos" in " ".join(s).lower() for s in names),
        f"completo {pedido.get('completo')}, etapa {chat.stage()}, selecciones {names}",
    )
    await chat.say("Mi DNI es 12345678 y me llamo Ana")
    rep.check(
        "E4.3 registra lo válido y pide solo lo que falta, sin métodos de pago aún",
        "registrar_datos_cliente" in chat.tools() and "enviar_metodos_pago" not in chat.tools(),
        f"faltan {chat.facts().get('datos_faltantes')}",
    )
    dbg_text = str(chat.last.get("debug"))
    rep.check("Datos personales enmascarados en el debug (DNI)", "12345678" not in dbg_text)
    await chat.say("Quispe Rojas, ana@example.com, 987654321")
    sent_methods = "enviar_metodos_pago" in chat.tools()
    if not sent_methods:
        await chat.say("Sí, pásame los métodos de pago")
        sent_methods = "enviar_metodos_pago" in chat.tools()
    rep.check(
        "E4.4-5 datos completos sin pedir dirección; envía los métodos de pago",
        sent_methods and "direccion" not in (chat.facts().get("datos_faltantes") or []),
        f"etapa {chat.stage()}",
    )
    await chat.say("¿Cuáles eran las cuentas?")
    rep.check(
        "E4.6 no reenvía los métodos de pago (una vez por pedido)",
        "enviar_metodos_pago" not in chat.tools()
        or "omitido" in str((chat.last.get("debug") or {}).get("herramientas_llamadas")),
    )

    rep.h("Escenario 5 — comprobante y confirmación")
    await chat.image("no-comprobante.png")
    orders = await _orders(api, headers)
    rep.check(
        "E5.1 una foto que no es comprobante no crea comprobante ni cambia la etapa",
        all(not o["tiene_comprobante"] for o in orders) and chat.stage() == "COBRO",
        f"etapa {chat.stage()}",
    )
    await chat.image("comprobante-monto.png")
    orders = await _orders(api, headers)
    if not orders:
        rep.check(
            "E5.2 comprobante con otro monto crea un pedido Por confirmar", False, "no hay pedido"
        )
        rep.note("Se detiene el escenario 5: sin pedido no hay nada que revisar.")
        return
    order = orders[0]
    rep.check(
        "E5.2 comprobante con otro monto: pedido Por confirmar, marcado y no rechazado",
        order["estado"] == "por_confirmar" and "monto_distinto" in order["observaciones"],
        f"estado {order['estado']}, observaciones {order['observaciones']}",
    )
    rep.check(
        "E5.2 el agente no dice que el pago está confirmado",
        "confirmad" not in chat.text().lower() or "no" in chat.text().lower(),
        chat.text()[:160],
    )
    detail = await _detail(api, headers, order["id"])
    rep.check(
        "E5.3 detalle con ítems, selecciones, comprobante leído, observaciones e historial",
        bool(detail["items"])
        and bool(detail["comprobantes"])
        and bool(detail["historial"])
        and (detail["comprobantes"] or [{}])[0].get("monto") is not None,
        f"monto leído {(detail['comprobantes'] or [{}])[0].get('monto')}",
    )
    r = await api.post(
        f"{ORDERS}/{order['id']}/reject",
        headers=headers,
        json={"motivo": "El monto no coincide con el total (S/ 500.00)"},
    )
    rep.check("E5.4 rechazar devuelve el pedido a Borrador y avisa", r.status_code == 200, r.text)
    await chat.image("comprobante-ok.png")
    order = ((await _orders(api, headers)) or [order])[0]
    rep.check(
        "E5.5 comprobante nuevo correcto: Por confirmar, sin marca de monto",
        order["estado"] == "por_confirmar" and "monto_distinto" not in order["observaciones"],
        f"observaciones {order['observaciones']}",
    )
    r1 = await api.post(
        f"{ORDERS}/{order['id']}/confirm",
        headers=headers,
        json={"siguiente_paso": "Te enviamos tus certificados en 48 h"},
    )
    r2 = await api.post(f"{ORDERS}/{order['id']}/confirm", headers=headers, json={})
    rep.check(
        "E5.6 confirmar cambia el estado y avisa; la segunda confirmación es 409 (5c)",
        r1.status_code == 200 and r1.json()["aviso_cliente"] == "enviado" and r2.status_code == 409,
        f"{r1.json()} / segunda {r2.status_code}",
    )
    await chat.say("¿Cuándo me llegan?")
    rep.check(
        "E5.7 en CERRADO no pide comprobante ni reenvía métodos",
        chat.stage() == "CERRADO" and "enviar_metodos_pago" not in chat.tools(),
        chat.text()[:160],
    )
    s1 = await api.post(
        f"{ORDERS}/{order['id']}/status", headers=headers, json={"estado": "en_curso"}
    )
    s2 = await api.post(
        f"{ORDERS}/{order['id']}/status", headers=headers, json={"estado": "completado"}
    )
    rep.check(
        "E5.8 En curso y Completado funcionan en orden",
        s1.status_code == 200 and s2.status_code == 200,
    )
    listed = (await api.get(ORDERS, headers=headers)).json()["items"]
    rep.check("Los pedidos de prueba solo salen con la casilla", listed == [])


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
                {"nombre": "San Isidro", "costo": "8.00", "tiempo": "24 h"},
                {"nombre": "Callao", "costo": "12.00", "tiempo": "24 a 48 h"},
            ],
            "envio_gratis_desde": "150.00",
            "recojo_en_tienda": False,
            "notas": "Entregas de lunes a sábado.",
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
        json={
            "method": "yape",
            "instructions": "Yape al 912345678 a nombre de Tienda Demo. "
            "Cuando pagues, envía la foto del comprobante por aquí.",
        },
    )
    chat = Chat(api, headers, rep)
    await chat.open()

    rep.h("T1 — dos polos, delivery, comprobante, confirmación")
    await chat.say("Hola")
    await chat.say("Quiero 2 polos")
    order = (await _orders(api, headers) or [{}])[0]
    detail = await _detail(api, headers, order["id"]) if order else {}
    rep.check(
        "T1.2 2 polos: subtotal 70.00, descuento 35.00, total 35.00",
        (detail.get("subtotal"), detail.get("descuento"), detail.get("total"))
        == ("70.00", "35.00", "35.00"),
        f"{detail.get('subtotal')} / {detail.get('descuento')} / {detail.get('total')}",
    )
    await chat.say("Soy Ana Quispe, mi número es 987654321")
    faltan = chat.facts().get("datos_faltantes") or []
    rep.check(
        "T1.3 no pregunta delivery o recojo (solo delivery); pide zona y dirección",
        "tipo_entrega" not in faltan and "direccion" in faltan,
        f"faltan {faltan}",
    )
    await chat.say("Delivery en Miraflores, Av. Larco 123")
    if "enviar_metodos_pago" not in chat.tools():
        await chat.say("Sí, pásame cómo pagar")
    detail = await _detail(api, headers, order["id"])
    rep.check(
        "T1.4 Miraflores: envío 8.00 y total 43.00",
        (detail.get("envio"), detail.get("total")) == ("8.00", "43.00"),
        f"envío {detail.get('envio')}, total {detail.get('total')}",
    )
    rep.check(
        "El agente dice el total del pedido (43.00), no uno propio",
        "43" in chat.text(),
        chat.text()[:200],
    )
    await chat.image("comprobante-ok.png")
    order = ((await _orders(api, headers)) or [NO_ORDER])[0]
    rep.check(
        "T1.6 comprobante: Por confirmar con 'El monto no coincide' (la captura dice 500)",
        order["estado"] == "por_confirmar" and "monto_distinto" in order["observaciones"],
        f"{order['observaciones']}",
    )
    r = await api.post(
        f"{ORDERS}/{order['id']}/confirm",
        headers=headers,
        json={"siguiente_paso": "Te lo entregamos mañana entre 2 y 6 pm"},
    )
    await chat.say("Gracias")
    rep.check(
        "T1.8 confirmar cierra la venta (CERRADO)",
        r.status_code == 200 and chat.stage() == "CERRADO",
    )

    rep.h("T1b — envío gratis")
    await chat.reset()
    await chat.say("Hola, quiero 2 mochilas")
    await chat.agree_if_asked()
    await chat.say("Ana Quispe, 987654321, Miraflores, Av. Larco 123")
    orders = await _orders(api, headers)
    mochilas = next((o for o in orders if o["estado"] == "borrador"), None)
    detail = await _detail(api, headers, mochilas["id"]) if mochilas else {}
    rep.check(
        "T1b 2 mochilas (240.00): envío 0.00",
        detail.get("envio") == "0.00" and detail.get("total") == "240.00",
        f"envío {detail.get('envio')}, total {detail.get('total')}",
    )

    rep.h("T2 — rechazo y corrección")
    await chat.reset()
    await chat.say("Hola, quiero 2 polos")
    await chat.agree_if_asked()
    await chat.say("Soy Ana Quispe, 987654321, delivery en Miraflores, Av. Larco 123")
    if "enviar_metodos_pago" not in chat.tools():
        await chat.say("Sí, pásame cómo pagar")
    await chat.image("comprobante-ok.png")
    order = next((o for o in await _orders(api, headers) if o["estado"] == "por_confirmar"), None)
    if order is None:
        rep.check(
            "T2.1 el comprobante deja el pedido Por confirmar", False, f"etapa {chat.stage()}"
        )
        rep.note("Se detiene T2: no hay pedido por confirmar que rechazar.")
        return
    await api.post(
        f"{ORDERS}/{order['id']}/reject",
        headers=headers,
        json={"motivo": "Falta el comprobante completo, envíalo de nuevo"},
    )
    await chat.say("Pásame de nuevo las cuentas")
    rep.check(
        "T2.2 tras un rechazo reenvía los métodos una vez",
        "enviar_metodos_pago" in chat.tools(),
        f"etapa {chat.stage()}",
    )
    await chat.say("Mejor agrégame una gorra")
    rep.check(
        "T2.3 cambiar el pedido saca al cliente de VERIFICACION",
        chat.stage() in ("COBRO", "DATOS", "PEDIDO"),
        f"etapa {chat.stage()}",
    )

    rep.h("T3 — contra entrega")
    config = db.get(AgentConfig, tid)
    assert config is not None
    config.process_options = {"modalidad_pago": "contra_entrega"}
    db.commit()
    DEFAULT_CACHE.invalidate(tid)
    await chat.reset()
    await chat.say("Hola, quiero una mochila y una gorra")
    await chat.agree_if_asked()
    await chat.say("Ana Quispe, 987654321, delivery al Callao, Jr. Unión 45")
    order = next((o for o in await _orders(api, headers) if o["estado"] == "borrador"), None)
    detail = await _detail(api, headers, order["id"]) if order else {}
    rep.check(
        "T3.2 145.00 + Callao 12.00 = 157.00",
        (detail.get("envio"), detail.get("total")) == ("12.00", "157.00"),
        f"envío {detail.get('envio')}, total {detail.get('total')}, "
        f"modalidad {detail.get('modalidad_pago')}",
    )
    rep.check(
        "T3.3 en contra entrega no manda cuentas",
        "enviar_metodos_pago" not in chat.tools(),
        chat.text()[:200],
    )
    await chat.say("Sí, confirmo")
    order = next((o for o in await _orders(api, headers) if o["estado"] == "por_confirmar"), None)
    rep.check(
        "T3.4 confirmar deja el pedido Por confirmar",
        order is not None and "gestionar_pedido" in chat.tools(),
        f"etapa {chat.stage()}",
    )
    if order:
        r = await api.post(
            f"{ORDERS}/{order['id']}/reject",
            headers=headers,
            json={"motivo": "No llegamos a esa zona esta semana"},
        )
        await chat.say("Ok, entonces qué hago")
        rep.check(
            "T3b rechazar contra entrega vuelve a COBRO, sin rechazo vigente",
            r.status_code == 200 and chat.stage() in ("COBRO", "DATOS", "PEDIDO"),
            f"etapa {chat.stage()}",
        )
