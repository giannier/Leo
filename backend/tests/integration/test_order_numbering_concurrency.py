"""Order numbers under real concurrency (Feature 004, TASK-425).

`OrderRepository.add_numbered` takes the number with one atomic
`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`. The only honest test of "no
duplicates" is several database connections creating orders at once, so this one
commits real rows (connections cannot share the suite's rolled-back transaction)
and removes them itself, whatever happens.
"""

from __future__ import annotations

import threading
import uuid

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order, TenantSequence
from app.models.tenant import Tenant
from app.orders.service import OrderService

WRITERS = 8
ORDERS_EACH = 3


def test_concurrent_orders_of_one_tenant_never_share_a_number(engine: Engine) -> None:
    setup = Session(engine)
    tenant = Tenant(name=f"Numeración {uuid.uuid4().hex[:6]}")
    setup.add(tenant)
    setup.flush()
    contact = Contact(tenant_id=tenant.id, phone_e164="+51987000222")
    setup.add(contact)
    setup.flush()
    conversations = [
        Conversation(tenant_id=tenant.id, channel="sandbox", contact_id=contact.id)
        for _ in range(WRITERS)
    ]
    setup.add_all(conversations)
    setup.flush()
    tenant_id = tenant.id
    conversation_ids = [conversation.id for conversation in conversations]
    contact_id = contact.id
    setup.commit()
    setup.close()

    start = threading.Barrier(WRITERS)
    errors: list[BaseException] = []

    def writer(conversation_id: uuid.UUID) -> None:
        try:
            with Session(engine) as session:
                conversation = session.get(Conversation, conversation_id)
                assert conversation is not None
                start.wait(10)
                for _ in range(ORDERS_EACH):
                    service = OrderService(session, tenant_id)
                    order = service.open_draft(
                        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=True
                    )
                    # Commit each one: the lock on the counter row is held until then.
                    order.estado = "descartado"
                    session.commit()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    try:
        threads = [threading.Thread(target=writer, args=(cid,)) for cid in conversation_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)

        assert errors == []
        with Session(engine) as check:
            numbers = sorted(
                check.execute(select(Order.numero).where(Order.tenant_id == tenant_id)).scalars()
            )
            assert numbers == list(range(1, WRITERS * ORDERS_EACH + 1))
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(delete(Order).where(Order.tenant_id == tenant_id))
            cleanup.execute(delete(TenantSequence).where(TenantSequence.tenant_id == tenant_id))
            cleanup.execute(delete(Conversation).where(Conversation.tenant_id == tenant_id))
            cleanup.execute(delete(Contact).where(Contact.id == contact_id))
            cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup.commit()
