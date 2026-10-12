"""Loading the facts of a conversation through the real database
(Feature 004, TASK-411).

`tests/unit/test_process_facts.py` checks every rule on plain objects. This file
checks what only the database can: that three statements are enough, that the
SQL joins return the right rows for an order in each state, and above all that a
business never reads another's order, proofs or customer data through the loader.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.conversation import Conversation
from app.process.engine import compute_stage
from app.process.facts import Facts, FactsConfig, FactsLoader
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.repositories.channel import ContactRepository
from app.repositories.conversation_snapshot import ConversationSnapshotRepository
from app.repositories.conversation_state import ConversationStateRepository
from app.repositories.orders import (
    OrderItemRepository,
    OrderRepository,
    PaymentProofRepository,
)
from app.schemas.business_info import Entregas
from tests.isolation.conftest import TenantFixture

NOW = datetime.now(UTC)
SHOP = TEMPLATES["productos"].resolve()


def _loader(
    db: Session, fixture: TenantFixture, *, entregas: Entregas | None = None
) -> FactsLoader:
    return FactsLoader(db, fixture.tenant_id, FactsConfig(options=SHOP, entregas=entregas))


def _conversation(db: Session, fixture: TenantFixture) -> Conversation:
    conversation = db.get(Conversation, fixture.conversation_id)
    assert conversation is not None
    # The fixture's conversation has no contact; the real channel always does.
    conversation.contact_id = fixture.contact_id
    db.flush()
    return conversation


def _order(db: Session, fixture: TenantFixture, **overrides: Any) -> Any:
    return OrderRepository(db, fixture.tenant_id).add_numbered(
        conversation_id=fixture.conversation_id,
        contact_id=fixture.contact_id,
        moneda="PEN",
        total=Decimal("500.00"),
        **overrides,
    )


def _line(db: Session, fixture: TenantFixture, order: Any, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "order_id": order.id,
        "nombre": "Paquete 5",
        "precio_unitario": Decimal("500.00"),
        "cantidad": 1,
        "subtotal": Decimal("500.00"),
    }
    values.update(overrides)
    return OrderItemRepository(db, fixture.tenant_id).add(**values)


def _proof(db: Session, fixture: TenantFixture, order: Any, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "order_id": order.id,
        "conversation_id": fixture.conversation_id,
        "storage_key": f"proofs/{uuid.uuid4()}.jpg",
    }
    values.update(overrides)
    return PaymentProofRepository(db, fixture.tenant_id).add(**values)


class TestAConversationNobodyHasAnsweredYet:
    def test_it_has_a_contact_and_nothing_else(self, db: Session, tenant_a: TenantFixture) -> None:
        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.saludo_enviado is False
        assert facts.pedido is None
        assert facts.pago_estado is None
        assert facts.nombre_cliente == "Customer of A"
        assert facts.nombre_confirmado is False
        assert facts.datos_faltantes == ["nombres"]  # the phone they write from counts

    def test_the_stage_is_inicio(self, db: Session, tenant_a: TenantFixture) -> None:
        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert compute_stage(TEMPLATES["productos"], SHOP, facts, NOW) is Stage.INICIO

    def test_a_conversation_without_a_contact_still_loads(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """The simulator's conversation has none."""
        conversation = db.get(Conversation, tenant_a.conversation_id)
        assert conversation is not None
        assert conversation.contact_id is None

        facts = _loader(db, tenant_a).load(conversation, now=NOW)

        assert facts.nombre_cliente is None
        assert facts.datos_faltantes == ["nombres", "telefono"]

    def test_a_conversation_without_a_contact_reads_its_stored_state(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        ConversationStateRepository(db, tenant_a.tenant_id).add(
            conversation_id=tenant_a.conversation_id,
            template_key="productos",
            template_version=1,
            stage_key="IDENTIFICAR",
            funnel_code="ATENDIDO",
            facts={"saludo_enviado": True, "camino": "x"},
        )
        conversation = db.get(Conversation, tenant_a.conversation_id)
        assert conversation is not None

        facts = _loader(db, tenant_a).load(conversation, now=NOW)

        assert facts.saludo_enviado is True
        assert facts.camino == "x"


class TestStoredFacts:
    def test_they_come_from_the_state_row(self, db: Session, tenant_a: TenantFixture) -> None:
        ConversationStateRepository(db, tenant_a.tenant_id).add(
            conversation_id=tenant_a.conversation_id,
            template_key="productos",
            template_version=1,
            stage_key="IDENTIFICAR",
            funnel_code="ATENDIDO",
            facts={
                "saludo_enviado": True,
                "vio_productos": True,
                "recursos_enviados": ["saludo"],
                "pago_estado": "confirmado",  # a derived key that must not be believed
            },
        )

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.saludo_enviado is True
        assert facts.vio_productos is True
        assert facts.recursos_enviados == ["saludo"]
        assert facts.pago_estado is None


class TestAnOrderInEachState:
    def _facts(self, db: Session, fixture: TenantFixture, **order: Any) -> Facts:
        placed = _order(db, fixture, **order)
        _line(db, fixture, placed)
        return _loader(db, fixture).load(_conversation(db, fixture), now=NOW)

    def test_a_draft(self, db: Session, tenant_a: TenantFixture) -> None:
        facts = self._facts(db, tenant_a)

        assert facts.pedido is not None
        assert (facts.pedido.numero, facts.pedido.estado) == (1, "borrador")
        assert facts.pedido.total == Decimal("500.00")
        assert facts.pago_estado is None

    def test_waiting_for_the_operator(self, db: Session, tenant_a: TenantFixture) -> None:
        facts = self._facts(db, tenant_a, estado="por_confirmar")

        assert facts.pago_estado == "por_verificar"

    @pytest.mark.parametrize("estado", ["confirmado", "en_curso", "completado"])
    def test_a_confirmed_order(self, db: Session, tenant_a: TenantFixture, estado: str) -> None:
        facts = self._facts(db, tenant_a, estado=estado)

        assert facts.pago_estado == "confirmado"

    def test_a_discarded_order_is_not_the_active_one(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        facts = self._facts(db, tenant_a, estado="descartado")

        assert facts.pedido is None
        assert facts.pago_estado is None

    def test_cash_on_delivery_uses_the_orders_modality(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        facts = self._facts(db, tenant_a, modalidad_pago="contra_entrega")

        assert facts.modalidad_pago == "contra_entrega"

    def test_the_newest_live_order_wins(self, db: Session, tenant_a: TenantFixture) -> None:
        old = _order(db, tenant_a, estado="confirmado")
        _line(db, tenant_a, old, nombre="Viejo")
        new = _order(db, tenant_a)
        _line(db, tenant_a, new, nombre="Nuevo")
        _order(db, tenant_a, estado="descartado")

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.pedido is not None
        assert facts.pedido.numero == 2
        assert [i.nombre for i in facts.pedido.items] == ["Nuevo"]
        assert facts.pago_estado is None  # the new draft, not the old confirmation

    def test_an_order_in_a_package_with_selections(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = _order(db, tenant_a)
        _line(
            db,
            tenant_a,
            order,
            bundle_size=5,
            bundle_selections=[{"nombre": "SIAF", "product_id": None}],
        )

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.pedido is not None
        assert facts.pedido.faltan_selecciones == 4
        assert facts.pedido.completo is False
        assert (
            compute_stage(
                TEMPLATES["productos"], SHOP, facts.model_copy(update={"saludo_enviado": True}), NOW
            )
            is Stage.PEDIDO
        )


class TestPaymentProofs:
    def test_a_rejected_proof_with_a_live_rejection(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = _order(db, tenant_a, rechazo_vigente=True, motivo_rechazo="Monto distinto")
        _line(db, tenant_a, order)
        _proof(db, tenant_a, order, estado="rechazado", reviewed_at=NOW)

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.pago_estado == "rechazado"
        assert facts.motivo_rechazo == "Monto distinto"

    def test_a_rejection_switched_off_returns_the_customer_to_the_flow(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = _order(db, tenant_a, rechazo_vigente=False)
        _line(db, tenant_a, order)
        _proof(db, tenant_a, order, estado="rechazado", reviewed_at=NOW)

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.pago_estado is None

    def test_only_the_latest_proof_counts(self, db: Session, tenant_a: TenantFixture) -> None:
        order = _order(db, tenant_a, rechazo_vigente=True)
        _line(db, tenant_a, order)
        _proof(db, tenant_a, order, estado="rechazado", reviewed_at=NOW - timedelta(hours=2))
        _proof(db, tenant_a, order, estado="por_verificar", observaciones=["monto_distinto"])

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        # The newest proof is pending, so the live rejection no longer holds.
        assert facts.pago_estado is None
        assert facts.comprobante is not None
        assert facts.comprobante.observaciones == ["monto_distinto"]

    def test_the_payment_methods_count_only_after_the_last_rejection(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = _order(db, tenant_a, rechazo_vigente=True)
        _line(db, tenant_a, order)
        rejected_at = NOW - timedelta(hours=1)
        _proof(db, tenant_a, order, estado="rechazado", reviewed_at=rejected_at)
        states = ConversationStateRepository(db, tenant_a.tenant_id)
        state = states.add(
            conversation_id=tenant_a.conversation_id,
            template_key="productos",
            template_version=1,
            stage_key="VERIFICACION",
            funnel_code="EN_VERIFICACION",
            facts={
                "saludo_enviado": True,
                "metodos_pago_enviados": {
                    "order_id": str(order.id),
                    "en": (rejected_at - timedelta(hours=3)).isoformat(),
                },
            },
        )

        before = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)
        state.facts = {
            "saludo_enviado": True,
            "metodos_pago_enviados": {
                "order_id": str(order.id),
                "en": (rejected_at + timedelta(minutes=5)).isoformat(),
            },
        }
        db.flush()
        after = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert before.metodos_pago_enviados is False
        assert after.metodos_pago_enviados is True

    def test_methods_sent_for_a_previous_order_do_not_count_for_the_new_one(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        old = _order(db, tenant_a, estado="completado")
        _line(db, tenant_a, old)
        new = _order(db, tenant_a)
        _line(db, tenant_a, new)
        ConversationStateRepository(db, tenant_a.tenant_id).add(
            conversation_id=tenant_a.conversation_id,
            template_key="productos",
            template_version=1,
            stage_key="COBRO",
            funnel_code="NEGOCIACION",
            facts={"metodos_pago_enviados": {"order_id": str(old.id), "en": NOW.isoformat()}},
        )

        facts = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)

        assert facts.metodos_pago_enviados is False


class TestCustomerDataAndDelivery:
    def test_partial_data_and_a_physical_product(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        contact = contacts.get(tenant_a.contact_id)
        assert contact is not None
        contacts.merge_custom_data(contact, {"nombres": "Ana"})
        order = _order(db, tenant_a)
        physical = Product(
            tenant_id=tenant_a.tenant_id, name="Libro", price=Decimal("30"), es_digital=False
        )
        db.add(physical)
        db.flush()
        _line(db, tenant_a, order, product_id=physical.id, nombre="Libro")
        entregas = Entregas(hace_delivery=True, recojo_en_tienda=True)

        facts = _loader(db, tenant_a, entregas=entregas).load(_conversation(db, tenant_a), now=NOW)

        assert facts.nombre_confirmado is True
        assert facts.requiere_entrega is True
        assert facts.datos_faltantes == ["tipo_entrega"]

    def test_a_digital_product_needs_no_delivery(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        contacts = ContactRepository(db, tenant_a.tenant_id)
        contact = contacts.get(tenant_a.contact_id)
        assert contact is not None
        contacts.merge_custom_data(contact, {"nombres": "Ana"})
        order = _order(db, tenant_a)
        digital = Product(
            tenant_id=tenant_a.tenant_id, name="Curso", price=Decimal("30"), es_digital=True
        )
        db.add(digital)
        db.flush()
        _line(db, tenant_a, order, product_id=digital.id, nombre="Curso")
        entregas = Entregas(hace_delivery=True, recojo_en_tienda=True)

        facts = _loader(db, tenant_a, entregas=entregas).load(_conversation(db, tenant_a), now=NOW)

        assert facts.requiere_entrega is False
        assert facts.datos_completos is True

    def test_a_line_whose_product_is_gone_counts_as_physical(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """Asking for an address nobody needed is better than not asking for one."""
        order = _order(db, tenant_a)
        _line(db, tenant_a, order, product_id=None)
        entregas = Entregas(hace_delivery=True, recojo_en_tienda=True)

        facts = _loader(db, tenant_a, entregas=entregas).load(_conversation(db, tenant_a), now=NOW)

        assert facts.requiere_entrega is True


class TestThreeStatementsAtMost:
    def _count(self, engine: Engine, db: Session, run: Any) -> int:
        statements: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            run()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    def test_a_full_conversation_takes_three(
        self, engine: Engine, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = _order(db, tenant_a, rechazo_vigente=True)
        _line(db, tenant_a, order)
        _line(db, tenant_a, order, nombre="Otro")
        _proof(db, tenant_a, order, estado="rechazado", reviewed_at=NOW)
        ConversationStateRepository(db, tenant_a.tenant_id).add(
            conversation_id=tenant_a.conversation_id,
            template_key="productos",
            template_version=1,
            stage_key="VERIFICACION",
            funnel_code="EN_VERIFICACION",
        )
        conversation = _conversation(db, tenant_a)
        db.expire_all()
        db.refresh(conversation)  # the caller already holds a loaded conversation
        loader = _loader(db, tenant_a)

        count = self._count(engine, db, lambda: loader.load(conversation, now=NOW))

        assert count == 3

    def test_a_conversation_without_an_order_takes_two(
        self, engine: Engine, db: Session, tenant_a: TenantFixture
    ) -> None:
        conversation = _conversation(db, tenant_a)
        db.expire_all()
        db.refresh(conversation)  # the caller already holds a loaded conversation
        loader = _loader(db, tenant_a)

        count = self._count(engine, db, lambda: loader.load(conversation, now=NOW))

        assert count == 2


class TestOneBusinessNeverReadsAnothers:
    def test_a_foreign_conversation_loads_as_empty(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        order = _order(db, tenant_b, estado="confirmado")
        _line(db, tenant_b, order)
        foreign = _conversation(db, tenant_b)

        facts = _loader(db, tenant_a).load(foreign, now=NOW)

        assert facts.pedido is None
        assert facts.pago_estado is None
        assert facts.nombre_cliente is None
        assert facts.saludo_enviado is False

    def test_the_snapshot_of_a_foreign_conversation_is_empty(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _order(db, tenant_b)
        foreign = _conversation(db, tenant_b)

        snapshot = ConversationSnapshotRepository(db, tenant_a.tenant_id).load(foreign)

        assert snapshot.state is None
        assert snapshot.contact is None
        assert snapshot.order is None
        assert snapshot.lines == []

    def test_a_conversation_tampered_to_point_at_a_foreign_contact_reads_no_contact(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Even holding tenant A's conversation, B's contact id yields nothing:
        the contact is joined with `tenant_id = A`."""
        conversation = db.get(Conversation, tenant_a.conversation_id)
        assert conversation is not None
        conversation.contact_id = tenant_b.contact_id
        db.flush()

        facts = _loader(db, tenant_a).load(conversation, now=NOW)

        assert facts.nombre_cliente is None

    def test_the_customers_data_is_not_shared_between_businesses(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = ContactRepository(db, tenant_a.tenant_id)
        contact = mine.get(tenant_a.contact_id)
        assert contact is not None
        mine.merge_custom_data(contact, {"nombres": "Ana", "dni": "12345678"})

        theirs = _loader(db, tenant_b).load(_conversation(db, tenant_b), now=NOW)

        assert theirs.nombre_confirmado is False
        assert theirs.nombre_cliente == "Customer of B"

    def test_the_active_order_of_one_business_is_not_the_others(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _line(db, tenant_a, _order(db, tenant_a), nombre="Solo de A")
        _line(db, tenant_b, _order(db, tenant_b), nombre="Solo de B")

        mine = _loader(db, tenant_a).load(_conversation(db, tenant_a), now=NOW)
        theirs = _loader(db, tenant_b).load(_conversation(db, tenant_b), now=NOW)

        assert mine.pedido is not None and theirs.pedido is not None
        assert [i.nombre for i in mine.pedido.items] == ["Solo de A"]
        assert [i.nombre for i in theirs.pedido.items] == ["Solo de B"]
        # Both are their business's order number 1.
        assert mine.pedido.numero == theirs.pedido.numero == 1

    def test_a_foreign_product_does_not_make_a_line_digital(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """A line pointing at another business's product id (a data bug) must not
        read that product."""
        foreign = Product(
            tenant_id=tenant_b.tenant_id, name="Curso B", price=Decimal("1"), es_digital=True
        )
        db.add(foreign)
        db.flush()
        order = _order(db, tenant_a)
        _line(db, tenant_a, order, product_id=foreign.id)
        entregas = Entregas(hace_delivery=True, recojo_en_tienda=True)

        facts = _loader(db, tenant_a, entregas=entregas).load(_conversation(db, tenant_a), now=NOW)

        assert facts.requiere_entrega is True  # the foreign product reads as unknown: physical
