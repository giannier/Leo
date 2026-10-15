"""Milestone C walked through the API with the real model (run by hand, not in CI).

    RUN_MILESTONE_C=1 LLM_PROFILE=openai OPENAI_API_KEY=... \
        uv run python -m pytest backend/tests/integration/test_zz_milestone_c_live.py -s

`milestones/C.md`: a business configured from scratch with the layers, the
education business no longer sounding like a shop (the complaint that started
the 017), a rule switched off, a draft situation tried and activated, and a new
rubro version that passes its evals, is published and shows up as news.
"""

from __future__ import annotations

import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_job_queue, get_platform_llm, get_sandbox_limiter
from app.core.config import get_settings
from app.models.user import User
from app.providers.llm.factory import build_provider, platform_profile
from app.repositories.catalog import CatalogRepository
from app.repositories.product import ProductRepository
from app.services.catalog_evals import run_evals
from app.services.situations import embed_pending
from tests.integration.test_knowledge_ingestion import RecordingQueue
from tests.integration.test_sandbox import NullRateLimiter
from tests.integration.test_zz_milestone_b_live import CAPACITA, Chat, Report, _business

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MILESTONE_C") != "1", reason="live walkthrough, run by hand"
)

A = "/api/v1/assistant"
TENANT = "/api/v1/tenant"
CAT = "/api/v1/platform-admin/catalog"
REPORT = Path(os.getenv("MILESTONE_C_REPORT") or Path(tempfile.gettempdir()) / "milestone_c.md")
SHOP_WORDS = ("carrito", "stock", "producto")


class DraftChat(Chat):
    """The simulator, trying draft situations when asked."""

    drafts: list[str] = []

    async def say(self, text: str) -> dict[str, Any]:
        r = await self.api.post(
            f"/api/v1/tenant/sandbox/conversations/{self.cid}/messages?debug=true",
            headers=self.headers,
            json={"content": text, "situaciones_prueba": self.drafts},
        )
        assert r.status_code == 200, r.text
        self.last = r.json()
        self.report.turn(text, self.last)
        dbg = self.last.get("debug") or {}
        if dbg.get("situaciones"):
            self.report.note(
                "situaciones: "
                + ", ".join(
                    f"{s['titulo']}{' (borrador)' if s['borrador'] else ''}"
                    f"{' d=' + str(s['distancia']) if s.get('distancia') is not None else ''}"
                    for s in dbg["situaciones"]
                )
            )
        return self.last

    def situations(self) -> list[str]:
        return [s["titulo"] for s in (self.last.get("debug") or {}).get("situaciones") or []]


async def test_milestone_c(api: AsyncClient, mail: Any, db: Session, app: Any) -> None:
    llm = build_provider(platform_profile(get_settings()))
    app.dependency_overrides[get_platform_llm] = lambda: llm
    app.dependency_overrides[get_sandbox_limiter] = NullRateLimiter
    queue = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    rep = Report()
    rep.lines[0] = "# Milestone C — recorrido automático con el modelo real\n"
    started = time.monotonic()
    try:
        await _walk(api, mail, db, rep, llm)
    finally:
        rep.lines.insert(0, f"Duración: {time.monotonic() - started:.0f} s\n")
        REPORT.write_text(  # noqa: ASYNC240 - once, after the walk
            "\n".join(
                rep.lines
                + ["", "## Resumen", ""]
                + [
                    f"- {'✅' if ok else '❌'} {name}{f' — {detail}' if detail else ''}"
                    for name, ok, detail in rep.checks
                ]
            )
            + "\n",
            encoding="utf-8",
        )


async def _walk(api: AsyncClient, mail: Any, db: Session, rep: Report, llm: Any) -> None:
    # ---------------------------------------------------------------- C1
    rep.h("C1 — Un instituto configurado desde cero con capas")
    headers, tid = await _business(api, mail, "Escuela Capacita", "instituto")
    templates = (await api.get(f"{A}/templates", headers=headers)).json()["items"]
    suggested = next(t["key"] for t in templates if t["sugerida_por_rubro"])
    await api.put(f"{A}/process", headers=headers, json={"template_key": suggested})
    products = ProductRepository(db, tid)
    for name, price, category, size in CAPACITA:
        products.add(
            name=name,
            price=Decimal(price),
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
            "instructions": "Yape al 987654321 a nombre de Escuela Capacita SAC.",
            "titular": "Escuela Capacita SAC",
            "validate_receipts": True,
            "is_active": True,
        },
    )
    behavior = await api.put(
        f"{A}/behavior",
        headers=headers,
        json={
            "tratamiento": "usted",
            "emojis": "pocos",
            "saludo": "¡Bienvenido a Escuela Capacita!",
        },
    )
    faq = await api.post(
        f"{A}/faqs",
        headers=headers,
        json={
            "pregunta": "¿El certificado es virtual?",
            "respuesta": "Sí, se envía en PDF al correo en 48 h.",
        },
    )
    overview = (await api.get(f"{A}/overview", headers=headers)).json()
    rep.check(
        "La plantilla sugerida para instituto es certificaciones",
        suggested == "certificaciones",
        suggested,
    )
    rep.check(
        "Perfil, FAQ y pago con titular se guardan",
        behavior.status_code == 200 and faq.status_code == 201,
    )
    rep.check(
        "El resumen avanza",
        overview["progreso"] > 0,
        f"progreso {overview['progreso']} %, siguiente {overview['siguiente_paso']}",
    )

    rep.h("C2 — El tono de un instituto (la queja original)")
    chat = DraftChat(api, headers, rep)
    await chat.open()
    await chat.say("Hola, buenas tardes")
    await chat.say("Me llamo Ana. ¿Qué me ofrecen?")
    await chat.say("Quiero certificar mi experiencia en SIAF, ¿para qué me sirve el certificado?")
    said = " ".join(
        str(s.get("texto") or s.get("body") or "")
        for turn in [chat.last]
        for s in turn.get("salientes") or []
    ).lower()
    rep.check(
        "Habla de certificados, no de productos de tienda",
        not any(word in said for word in SHOP_WORDS) and "certific" in said,
        said[:200],
    )
    rep.check(
        "Trata de usted (perfil)", " tú " not in f" {said} " and "tienes" not in said, said[:120]
    )

    # ---------------------------------------------------------------- C3
    rep.h("C3 — Apagar una regla del rubro")
    rubro = (await api.get(f"{A}/rubro", headers=headers)).json()
    rule = next(r for r in rubro["reglas"] if r["rule_key"] == "rubro.vocabulario")
    switched = await api.put(
        f"{A}/rules/rubro/rubro.vocabulario", headers=headers, json={"apagada": True}
    )
    preview = (
        await api.get(f"{A}/prompt-preview", headers=headers, params={"stage": "IDENTIFICAR"})
    ).json()
    await chat.say("¿Y el de Excel avanzado?")
    rep.check(
        "La regla apagada sale del prompt y la depuración la muestra",
        switched.status_code == 200
        and rule["texto"][:40] not in preview["prompt"]
        and "rubro.vocabulario" in (chat.last["debug"]["reglas_apagadas"].get("rubro") or []),
    )
    await api.put(f"{A}/rules/rubro/rubro.vocabulario", headers=headers, json={"apagada": False})

    # ---------------------------------------------------------------- C4
    rep.h("C4 — Una situación: borrador, prueba, activación")
    created = (
        await api.post(
            f"{A}/situations",
            headers=headers,
            json={
                "titulo": "Si piden factura",
                "cuando_aplica": "El cliente pide factura o boleta con RUC",
                "que_hace": (
                    "Pídele su RUC y razón social, y dile que la factura llega con la "
                    "confirmación del pago."
                ),
            },
        )
    ).json()
    await embed_pending(db, tid, llm)
    db.commit()
    await chat.reset()
    await chat.say("Hola, ¿me pueden dar factura?")
    without = chat.situations()
    chat.drafts = [created["id"]]
    await chat.say("Necesito factura con RUC, por favor")
    with_draft = chat.situations()
    reply = chat.text()
    chat.drafts = []
    await api.post(f"{A}/situations/{created['id']}/activar", headers=headers)
    await chat.reset()
    await chat.say("Buenas, ¿emiten factura?")
    active = chat.situations()
    rep.check("Un borrador no entra sin pedirlo", "Si piden factura" not in without, str(without))
    rep.check(
        "Probado en el simulador entra y se cumple",
        "Si piden factura" in with_draft and "ruc" in reply.lower(),
        reply[:160],
    )
    rep.check("Activa, entra sola por similitud", "Si piden factura" in active, str(active))

    # ---------------------------------------------------------------- C5
    rep.h("C5 — Una versión nueva del rubro: evals, publicación y novedad")
    user = db.query(User).filter(User.email.like("b-%")).order_by(User.created_at.desc()).first()
    assert user is not None
    user.is_platform_admin = True
    db.commit()
    draft = (await api.post(f"{CAT}/rubro/instituto/versions", headers=headers)).json()
    body = {k: draft[k] for k in ("reglas", "faqs", "objeciones", "situaciones_sugeridas")}
    body["reglas"].append(
        {
            "rule_key": "rubro.horas_lectivas",
            "texto": (
                "Si preguntan cuántas horas tiene un certificado, respóndelo solo con el "
                "catálogo o el conocimiento del negocio."
            ),
        }
    )
    await api.put(f"{CAT}/rubro/instituto/versions/{draft['version']}", headers=headers, json=body)
    early = await api.post(
        f"{CAT}/rubro/instituto/versions/{draft['version']}/publicar", headers=headers
    )
    version = CatalogRepository(db).version("rubro", "instituto", draft["version"])
    assert version is not None
    run = await run_evals(db, version, llm)
    db.commit()
    rep.note("evals: " + ", ".join(f"{'✓' if c.ok else '✗'} {c.nombre}" for c in run.casos))
    published = await api.post(
        f"{CAT}/rubro/instituto/versions/{draft['version']}/publicar", headers=headers
    )
    news = (await api.get(f"{A}/rubro", headers=headers)).json()["novedades"]
    rep.check("Sin evals no se publica", early.status_code == 409)
    rep.check("Los evals del borrador pasan con el modelo real", run.ok)
    rep.check(
        "Publicada, el negocio ve la novedad",
        published.status_code == 200
        and bool(news)
        and any(r["rule_key"] == "rubro.horas_lectivas" for r in news["reglas"]),
        str(news)[:160],
    )
    await api.post(f"{A}/novedades/rubro/vistas", headers=headers)
    rep.check(
        "Marcada como vista, desaparece",
        (await api.get(f"{A}/rubro", headers=headers)).json()["novedades"] is None,
    )

    # ---------------------------------------------------------------- C6
    rep.h("C6 — Costo por mensaje")
    tokens = (chat.last.get("debug") or {}).get("tokens") or {}
    rep.check(
        "Un turno con capas y una situación queda bajo 6 000 tokens de entrada",
        0 < int(tokens.get("prompt") or 0) < 6000,
        str(tokens),
    )
