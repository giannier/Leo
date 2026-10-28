"""Simulated conversations (feature 020, T2.7): a person or Claude plays the model; no API call.

The real turn pipeline runs (stage engine, prompt, tools, order engine, agenda, database) inside a
transaction that is always rolled back. Where the model would answer, the harness takes the next
step of the scenario (`escenarios/*.json`). When the script runs out, it writes the exact call the
model would receive (tools, prompt, history) to `<escenario>.pending.txt` and stops, so the next
step can be decided reading it; the transcript goes to `<escenario>.log.txt`. Both land in
`$LEO_SIMULACION` (default: the system's temporary folder, `leo-simulacion`).

What it does not measure: how the real model reads the prompt. The situations that enter by meaning
are chosen by hand per turn (`"situaciones"`), because their vectors need the embedding API.

    DATABASE_URL=... PYTHONPATH=backend uv run --project backend \\
        python backend/evals/simulacion/harness.py backend/evals/simulacion/escenarios/spa.json

A scenario: `{"negocio": "spa", "turnos": [{"cliente": "...", "situaciones": ["clave"],
"agente": [{"tools": [{"name": "...", "args": {...}}]}, {"texto": "..."}]}]}`. In the arguments,
`{id:<nombre del producto>}` becomes that product's id.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from tests.integration.test_scheduling import HOURS
from tests.integration.test_turn_pipeline import _conversation, _pipeline, _say, _tenant

import app.agent.pipeline as pipeline_module
from app.agent.pipeline import InboundTurn
from app.agent.situations import SelectedSituation, _selected
from app.core.config import get_settings
from app.models.business import AgentConfig, BusinessProfile, PaymentMethodBusiness, Product
from app.models.tenant import Tenant
from app.process.service import DEFAULT_CACHE
from app.process.templates import UNIVERSAL
from app.providers.llm import ChatMessage, ChatResult, MockLLMProvider, ToolCall, ToolSpec
from app.repositories.situation import SituationRepository
from app.scheduling.service import Scheduling
from app.services.rubro_template import RubroTemplateService

# One clock for the prompt and the agenda: Wednesday 28 October 2026, 10:00 in Lima.
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=ZoneInfo("America/Lima"))
OUT = Path(os.getenv("LEO_SIMULACION") or Path(tempfile.gettempdir()) / "leo-simulacion")
YAPE = "Yape al 987 654 321 a nombre de Comercial Andina SAC"
PLACEHOLDER = re.compile(r"\{id:([^}]+)\}")


class Pause(BaseException):
    """The script ran out: the model's next answer is to be decided (not an `Exception`, so the
    pipeline's fallbacks do not swallow it)."""


class ScriptedModel(MockLLMProvider):
    def __init__(self, pending: Path) -> None:
        super().__init__()
        self.steps: list[ChatResult] = []
        self.pending = pending
        self.seen: list[list[ChatMessage]] = []

    async def chat(
        self, messages: Sequence[ChatMessage], *, tools: Sequence[ToolSpec] = (), **_: Any
    ) -> ChatResult:
        self.seen.append(list(messages))
        if not self.steps:
            self.pending.write_text(render(list(messages), list(tools)), encoding="utf-8")
            raise Pause
        return self.steps.pop(0)


def render(messages: list[ChatMessage], tools: list[ToolSpec]) -> str:
    """The call as the model would get it."""
    lines = ["TOOLS:"]
    for tool in tools:
        schema = json.dumps(tool.parameters, ensure_ascii=False)
        lines.append(f"* {tool.name}: {tool.description} {schema}")
    lines.append("")
    for message in messages:
        head = f"----- {message.role}"
        if message.tool_calls:
            calls = (
                f"{c.name}({json.dumps(c.arguments, ensure_ascii=False)})"
                for c in message.tool_calls
            )
            head += " → " + "; ".join(calls)
        lines += [head, message.content]
    return "\n".join(lines)


# ---------------------------------------------------------------- businesses


def _business(
    db: Session,
    name: str,
    industry: str | None,
    capacidades: tuple[str, ...] = ("atender", "vender"),
    **options: Any,
) -> Tenant:
    tenant = _tenant(db, template=UNIVERSAL)
    profile = db.execute(
        select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
    ).scalar_one()
    profile.business_name = name
    profile.industry = industry
    agent = db.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant.id)).scalar_one()
    agent.capacidades = list(capacidades)
    if options:
        agent.process_options = options
    db.add(PaymentMethodBusiness(tenant_id=tenant.id, method="yape", instructions=YAPE))
    db.flush()
    return tenant


def _items(db: Session, tenant: Tenant, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        db.add(Product(tenant_id=tenant.id, **{**row, "price": Decimal(str(row["price"]))}))
    db.flush()


def ferreteria(db: Session) -> Tenant:
    tenant = _business(
        db, "Ferretería El Tornillo", "hogar", datos_requeridos=["nombres", "telefono", "direccion"]
    )
    tools = "Herramientas"
    _items(db, tenant, [
        {"name": "Martillo de uña 16 oz", "price": 38, "category": tools},
        {"name": "Destornillador plano 6 mm", "price": 12, "category": tools},
        {"name": "Wincha 5 m", "price": 18, "category": tools},
        {"name": 'Alicate universal 8"', "price": 29, "category": tools},
        {"name": "Pintura látex blanca 1 galón", "price": 58, "category": "Pinturas"},
        {"name": "Kit 3 herramientas", "price": 60, "category": "Kits", "bundle_size": 3,
         "bundle_categoria": tools, "description": "Elige 3 herramientas de mano"},
        {"name": "Instalación de tomacorrientes", "price": 0, "tipo": "servicio",
         "modo_venta": "cotizacion", "description": "Visita técnica y presupuesto según puntos"},
    ])  # fmt: skip
    return tenant


def instituto(db: Session) -> Tenant:
    tenant = _business(db, "Instituto Aprende Perú", "instituto")
    certs = "Certificaciones"
    _items(db, tenant, [
        {"name": "Certificado en Excel empresarial", "price": 120, "category": certs,
         "tipo": "servicio"},
        {"name": "Certificado en Gestión de almacenes", "price": 120, "category": certs,
         "tipo": "servicio"},
        {"name": "Certificado en Seguridad y salud en el trabajo", "price": 120, "category": certs,
         "tipo": "servicio"},
        {"name": "Pack 3 certificados", "price": 300, "category": "Packs", "tipo": "servicio",
         "bundle_size": 3, "bundle_categoria": certs, "bundle_permite_libres": True},
        {"name": "Diplomado en Logística y cadena de suministro", "price": 0,
         "category": "Programas", "tipo": "servicio", "modo_venta": "informar"},
    ])  # fmt: skip
    RubroTemplateService(db, tenant.id).apply(
        {
            "educacion.factura": {"plazo_emision": "72 horas"},
            "educacion.grupo": {"minimo_personas": "5"},
        },
        by=None,
    )
    return tenant


def spa(db: Session) -> Tenant:
    tenant = _business(db, "Spa Raíces", "belleza", ("atender", "vender", "agendar"))
    profile = db.execute(
        select(BusinessProfile).where(BusinessProfile.tenant_id == tenant.id)
    ).scalar_one()
    profile.opening_hours = HOURS
    profile.timezone = "America/Lima"
    _items(db, tenant, [
        {"name": "Limpieza facial profunda", "price": 95, "tipo": "servicio", "agendable": True,
         "duracion_min": 60, "category": "Faciales"},
        {"name": "Masaje descontracturante", "price": 130, "tipo": "servicio", "agendable": True,
         "duracion_min": 60, "category": "Masajes"},
        {"name": "Crema hidratante de aloe 200 ml", "price": 49, "category": "Productos"},
    ])  # fmt: skip
    Scheduling(db, tenant.id).ensure_resource()
    return tenant


def uniformes(db: Session) -> Tenant:
    tenant = _business(
        db,
        "Confecciones Lumen",
        "b2b",
        ("atender", "vender", "cotizar"),
        datos_requeridos=["nombres", "telefono", "ruc"],
    )
    _items(db, tenant, [
        {"name": "Polo piqué con logo bordado", "price": 34, "category": "Polos"},
        {"name": "Uniformes corporativos por volumen", "price": 0, "modo_venta": "cotizacion",
         "category": "Corporativo",
         "description": "Desde 30 prendas; precio según modelo, tela y bordado"},
        {"name": "Diseño de colección para marca propia", "price": 0, "tipo": "servicio",
         "modo_venta": "informar", "category": "Diseño"},
    ])  # fmt: skip
    return tenant


BUSINESSES: dict[str, Callable[[Session], Tenant]] = {
    "ferreteria": ferreteria,
    "instituto": instituto,
    "spa": spa,
    "uniformes": uniformes,
}


# ------------------------------------------------------------------- running


def _resolve(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda match: ids[match.group(1)], value)
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    return value


def _steps(agent: list[dict[str, Any]], ids: dict[str, str]) -> list[ChatResult]:
    steps = []
    for step in agent:
        calls = tuple(
            ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name=call["name"],
                arguments=_resolve(call.get("args", {}), ids),
            )
            for call in step.get("tools", [])
        )
        steps.append(ChatResult(content=step.get("texto", ""), tool_calls=calls))
    return steps


def _results(seen: list[list[ChatMessage]]) -> list[str]:
    """The tool results each call of the turn received (after the model's last request)."""
    out = []
    for messages in seen[1:]:
        last = max(i for i, m in enumerate(messages) if m.role == "assistant")
        out += [
            f"   ← {m.name}: {m.content[:1200]}" for m in messages[last + 1 :] if m.role == "tool"
        ]
    return out


def run(scenario_path: Path) -> None:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    pending = OUT / f"{scenario_path.stem}.pending.txt"
    log = OUT / f"{scenario_path.stem}.log.txt"
    lines = asyncio.run(_play(scenario, pending))
    log.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


async def _play(scenario: dict[str, Any], pending: Path) -> list[str]:
    engine = create_engine(get_settings().database_url)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    Scheduling.now = lambda self: NOW  # type: ignore[method-assign]
    picked: list[str] = []

    async def selector(
        session: Session, tenant_id: uuid.UUID, *, stage: str, **_: Any
    ) -> list[SelectedSituation]:
        """The «siempre» ones, plus those the turn names (the semantic match needs vectors)."""
        candidates = SituationRepository(session, tenant_id).candidates(stage)
        chosen = [c for c in candidates if c.siempre]
        chosen += [c for c in candidates if not c.siempre and c.plantilla_clave in picked]
        return [_selected(c, {}, None if c.siempre else 0.3) for c in chosen[:5]]

    setattr(pipeline_module, "select_situations", selector)  # noqa: B010 - the selector is swapped
    lines: list[str] = []
    try:
        tenant = BUSINESSES[scenario["negocio"]](db)
        DEFAULT_CACHE.invalidate(tenant.id)
        products = db.execute(select(Product).where(Product.tenant_id == tenant.id)).scalars()
        ids = {p.name: str(p.id) for p in products}
        conversation = _conversation(db, tenant, channel="whatsapp")
        model = ScriptedModel(pending)
        for n, turn in enumerate(scenario["turnos"], start=1):
            picked[:] = turn.get("situaciones", [])
            message = _say(db, conversation, turn["cliente"])
            lines.append(f"\n=== Turno {n} · CLIENTE: {turn['cliente']}")
            model.steps = _steps(turn.get("agente", []), ids)
            model.seen = []
            try:
                result = await _pipeline(db, tenant, conversation, model, clock=lambda: NOW).run(
                    conversation.id, InboundTurn(text=turn["cliente"], message_ids=(message.id,))
                )
            except Pause:
                lines += _results(model.seen)
                lines.append(f"[PAUSA: el modelo debe responder; ver {pending}]")
                break
            lines += _results(model.seen)
            lines.append(
                f"   etapa {result.stage_before.value} → {result.stage_after.value}"
                f" · {result.outcome} · herramientas: {', '.join(result.tools_enabled)}"
            )
            lines += [
                f"   [enviado por herramienta] {m.msg_type}: {(m.content or '')[:300]}"
                for m in result.tool_messages
            ]
            lines += [f"   LEO: {m.content}" for m in result.text_messages]
            db.flush()
        else:
            lines.append("\n[fin del guion]")
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
    return lines


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    (OUT / f"{target.stem}.pending.txt").unlink(missing_ok=True)
    run(target)
