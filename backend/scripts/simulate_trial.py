"""Run a whole 14-day trial in a few seconds, against the local database.

Milestone 4 of `tasks.md`: "simular un ciclo completo de trial con reloj
acelerado". The lifecycle jobs take `now` as an argument rather than reading
the wall clock, so this walks a real tenant through every transition by
handing them different dates - no clock patching, no waiting two weeks.

    make simulate-trial

It creates its own tenant, prints what happens on each simulated day, and
deletes everything it created on the way out. Nothing else in the database is
touched. Emails are captured and summarised rather than sent.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.db import get_session_factory
from app.models.audit import AuditLog
from app.models.business import AgentConfig
from app.models.conversation import Conversation, Message
from app.models.plan import Plan
from app.models.tenant import Tenant, TenantNotification, TenantUser
from app.models.user import User
from app.providers.email.mock import MockEmailProvider
from app.services.trial import agent_may_reply, record_ai_message
from app.workers.trial_lifecycle import (
    run_tenant_archiver,
    run_trial_enforcer,
    run_trial_notifier,
)

DAY_ZERO = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
TRIAL_DAYS = 14


def _say(day: int, text: str) -> None:
    label = f"day {day:>3}" if day >= 0 else "setup  "
    print(f"  {label}  {text}")


def _mail_since(mail: MockEmailProvider, seen: int) -> list[str]:
    subjects = [message.subject for message in mail.sent[seen:]]
    return subjects


async def main(*, keep: bool) -> int:
    session_factory = get_session_factory()
    mail = MockEmailProvider()
    marker = uuid.uuid4().hex[:8]

    with session_factory() as session:
        plan = session.get(Plan, "trial")
        if plan is None:
            print("No 'trial' plan in the database. Run `make migrate` first.")
            return 1

        tenant = Tenant(
            name=f"[simulacion {marker}] Panaderia Quispe",
            plan_id="trial",
            status="trial",
            trial_ends_at=DAY_ZERO + timedelta(days=TRIAL_DAYS),
        )
        session.add(tenant)
        session.flush()

        user = User(
            full_name="Ana Quispe",
            email=f"simulacion-{marker}@example.invalid",
            password_hash="x",
            phone_e164=f"+51900{marker[:6]}",
            email_verified_at=DAY_ZERO,
            phone_verified_at=DAY_ZERO,
        )
        session.add(user)
        session.flush()
        session.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role="owner"))
        session.add(
            AgentConfig(
                tenant_id=tenant.id,
                is_active=True,
                farewell_on_pause="Gracias por escribir. Volvemos apenas reactivemos el servicio.",
            )
        )
        thread = Conversation(tenant_id=tenant.id, channel="whatsapp", status="open")
        session.add(thread)
        session.commit()

        tenant_id = tenant.id
        print()
        print(f"Tenant de prueba: {tenant.name}")
        print(f"  plan trial: {plan.max_ai_messages} mensajes, {TRIAL_DAYS} dias")
        print(f"  vence: {tenant.trial_ends_at:%Y-%m-%d}")
        print()
        print("Recorrido del trial")
        print("-" * 68)

        _say(-1, f"agente puede responder: {agent_may_reply(session, tenant)}")

        for day in (1, 5, 10, 11, 12, 13, 14, 15):
            now = DAY_ZERO + timedelta(days=day)
            seen = len(mail.sent)

            await run_trial_notifier(session, email_provider=mail, now=now)
            await run_trial_enforcer(session, email_provider=mail, now=now)

            session.refresh(tenant)
            subjects = _mail_since(mail, seen)
            note = f"estado={tenant.status}"
            if subjects:
                note += "  |  email: " + "; ".join(subjects)
            _say(day, note)

        session.refresh(thread)
        closing = session.execute(
            select(Message)
            .where(Message.conversation_id == thread.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        print()
        print(f"  conversacion whatsapp: {thread.status}")
        if closing is not None:
            print(f"  ultimo mensaje ({closing.author}): {closing.content}")
        print(f"  agente puede responder ahora: {agent_may_reply(session, tenant)}")

        # --- the other way a trial ends -----------------------------------
        print()
        print("Y ahora el otro final: agotar la cuota antes de la fecha")
        print("-" * 68)
        tenant.status = "trial"
        tenant.paused_at = None
        tenant.ai_messages_used = 0
        tenant.trial_ends_at = DAY_ZERO + timedelta(days=TRIAL_DAYS)
        session.execute(delete(TenantNotification).where(TenantNotification.tenant_id == tenant_id))
        thread.status = "open"
        session.commit()

        for _ in range(plan.max_ai_messages - 1):
            record_ai_message(session, tenant_id, channel="whatsapp")
        session.commit()
        session.refresh(tenant)
        _say(3, f"usados {tenant.ai_messages_used}/{plan.max_ai_messages} - sigue respondiendo")

        # Sandbox is free: this one must not move the counter.
        record_ai_message(session, tenant_id, channel="sandbox")
        session.commit()
        session.refresh(tenant)
        _say(3, f"tras 1 mensaje de sandbox: {tenant.ai_messages_used} (no cuenta)")

        record_ai_message(session, tenant_id, channel="whatsapp")
        session.commit()
        session.refresh(tenant)
        _say(3, f"usados {tenant.ai_messages_used}/{plan.max_ai_messages} - cuota agotada")
        _say(3, f"agente puede responder: {agent_may_reply(session, tenant)}  (antes del job)")

        seen = len(mail.sent)
        await run_trial_enforcer(
            session, email_provider=mail, now=DAY_ZERO + timedelta(days=3, hours=1)
        )
        session.refresh(tenant)
        _say(3, f"tras el enforcer: estado={tenant.status}")
        for subject in _mail_since(mail, seen):
            _say(3, f"email: {subject}")

        # --- retention ----------------------------------------------------
        print()
        print("Retencion de 30 dias")
        print("-" * 68)
        paused_on = tenant.paused_at or DAY_ZERO
        for day_offset in (29, 31):
            now = paused_on + timedelta(days=day_offset)
            await run_tenant_archiver(session, now=now)
            session.refresh(tenant)
            print(f"  +{day_offset} dias pausado  estado={tenant.status}")

        print()
        print("Auditoria y avisos registrados")
        print("-" * 68)
        kinds = (
            session.execute(
                select(TenantNotification.kind).where(TenantNotification.tenant_id == tenant_id)
            )
            .scalars()
            .all()
        )
        print(f"  avisos enviados una sola vez: {sorted(kinds)}")
        print(f"  emails totales: {len(mail.sent)}")

        if keep:
            print()
            print(f"Tenant conservado para inspeccion: {tenant_id}")
            return 0

        # Cleanup, children first.
        session.execute(delete(Message).where(Message.tenant_id == tenant_id))
        session.execute(delete(Conversation).where(Conversation.tenant_id == tenant_id))
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(TenantNotification).where(TenantNotification.tenant_id == tenant_id))
        session.execute(delete(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        session.execute(delete(TenantUser).where(TenantUser.tenant_id == tenant_id))
        session.execute(delete(User).where(User.id == user.id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
        print()
        print("Datos de la simulacion eliminados.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="No borrar el tenant de prueba, para inspeccionarlo en pgAdmin.",
    )
    raise SystemExit(asyncio.run(main(keep=parser.parse_args().keep)))
