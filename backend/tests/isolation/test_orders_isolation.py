"""One business never reaches another's orders, items, proofs or order numbers
(Feature 004, TASK-407).

Orders are the most sensitive rows the engine writes: they carry the customer's
data and what was sold. The cases hand tenant A tenant B's real ids and expect
nothing back, and they check the numbering that gives each business its own
order #1.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.orders import ORDERS_SEQUENCE, Order
from app.repositories.base import TenantScopeError
from app.repositories.orders import (
    OrderItemRepository,
    OrderRepository,
    PaymentProofRepository,
    TenantSequenceRepository,
)
from tests.isolation.conftest import TenantFixture


def _order(fixture: TenantFixture, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "conversation_id": fixture.conversation_id,
        "contact_id": fixture.contact_id,
        "moneda": "PEN",
    }
    values.update(overrides)
    return values


def _item(order: Order, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_id": order.id,
        "nombre": "Paquete 5",
        "precio_unitario": Decimal("500.00"),
        "cantidad": 1,
        "subtotal": Decimal("500.00"),
    }
    values.update(overrides)
    return values


def _proof(fixture: TenantFixture, order: Order, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_id": order.id,
        "conversation_id": fixture.conversation_id,
        "storage_key": f"proofs/{uuid.uuid4()}.jpg",
    }
    values.update(overrides)
    return values


@pytest.fixture
def seeded(db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture) -> dict[str, Order]:
    orders: dict[str, Order] = {}
    for label, fixture in (("a", tenant_a), ("b", tenant_b)):
        order = OrderRepository(db, fixture.tenant_id).add_numbered(**_order(fixture))
        OrderItemRepository(db, fixture.tenant_id).add(**_item(order, nombre=f"Item {label}"))
        PaymentProofRepository(db, fixture.tenant_id).add(
            **_proof(fixture, order, codigo_operacion=f"OP-{label}")
        )
        orders[label] = order
    return orders


class TestEachTenantNumbersItsOwnOrders:
    def test_two_tenants_each_get_order_number_one(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        assert (seeded["a"].numero, seeded["b"].numero) == (1, 1)

    def test_numbers_grow_by_one_inside_a_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        orders = OrderRepository(db, tenant_a.tenant_id)
        numbers = [orders.add_numbered(**_order(tenant_a)).numero for _ in range(3)]
        other = OrderRepository(db, tenant_b.tenant_id).add_numbered(**_order(tenant_b))

        assert numbers == [1, 2, 3]
        assert other.numero == 1

    def test_the_sequence_reports_its_current_value_per_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        mine = TenantSequenceRepository(db, tenant_a.tenant_id)
        theirs = TenantSequenceRepository(db, tenant_b.tenant_id)
        assert mine.current(ORDERS_SEQUENCE) == 0

        assert [mine.next_value(ORDERS_SEQUENCE) for _ in range(2)] == [1, 2]

        assert mine.current(ORDERS_SEQUENCE) == 2
        assert theirs.current(ORDERS_SEQUENCE) == 0

    def test_sequences_with_different_names_are_independent(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        sequences = TenantSequenceRepository(db, tenant_a.tenant_id)

        assert sequences.next_value("orders") == 1
        assert sequences.next_value("invoices") == 1
        assert sequences.next_value("orders") == 2

    def test_a_sequence_is_not_addressable_by_id(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        with pytest.raises(NotImplementedError):
            TenantSequenceRepository(db, tenant_a.tenant_id).get(uuid.uuid4())

    def test_the_number_cannot_be_chosen(self, db: Session, tenant_a: TenantFixture) -> None:
        with pytest.raises(ValueError, match="numero"):
            OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a), numero=99)

    def test_a_duplicate_number_in_the_same_tenant_fails(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        orders = OrderRepository(db, tenant_a.tenant_id)
        orders.add(**_order(tenant_a), numero=7)

        with pytest.raises(IntegrityError), db.begin_nested():
            orders.add(**_order(tenant_a), numero=7)

    def test_the_same_number_in_another_tenant_is_accepted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        OrderRepository(db, tenant_a.tenant_id).add(**_order(tenant_a), numero=7)

        other = OrderRepository(db, tenant_b.tenant_id).add(**_order(tenant_b), numero=7)

        assert other.numero == 7


class TestOrdersArePerTenant:
    def test_a_foreign_order_is_invisible(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        repository = OrderRepository(db, tenant_a.tenant_id)

        assert repository.get(seeded["b"].id) is None
        assert not repository.exists(seeded["b"].id)
        assert repository.update(seeded["b"].id, estado="confirmado") is None
        assert repository.delete(seeded["b"].id) is False
        db.refresh(seeded["b"])
        assert seeded["b"].estado == "borrador"

    def test_listing_counting_and_lookup_by_number_are_scoped(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        repository = OrderRepository(db, tenant_a.tenant_id)

        assert [o.id for o in repository.list()] == [seeded["a"].id]
        assert repository.count() == 1
        # Both tenants have an order #1: each finds its own.
        by_number = repository.by_numero(1)
        assert by_number is not None and by_number.id == seeded["a"].id

    def test_a_foreign_conversation_has_no_orders_for_you(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        repository = OrderRepository(db, tenant_a.tenant_id)

        assert repository.for_conversation(tenant_b.conversation_id) == []
        assert repository.active_for_conversation(tenant_b.conversation_id) is None

    def test_a_repository_cannot_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        with pytest.raises(TenantScopeError):
            OrderRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, numero=1, **_order(tenant_b)
            )

    def test_deleting_all_of_your_orders_leaves_the_other_alone(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        # Proofs have no cascade, so they go first, as a tenant wipe would.
        PaymentProofRepository(db, tenant_a.tenant_id).delete_all()

        assert OrderRepository(db, tenant_a.tenant_id).delete_all() == 1

        assert OrderRepository(db, tenant_b.tenant_id).count() == 1


class TestTheActiveOrder:
    def test_it_is_the_last_one_that_was_not_discarded(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        orders = OrderRepository(db, tenant_a.tenant_id)
        first = orders.add_numbered(**_order(tenant_a), estado="confirmado")
        second = orders.add_numbered(**_order(tenant_a))
        orders.add_numbered(**_order(tenant_a), estado="descartado")

        active = orders.active_for_conversation(tenant_a.conversation_id)

        assert active is not None
        assert active.id == second.id
        assert [o.numero for o in orders.for_conversation(tenant_a.conversation_id)] == [3, 2, 1]
        assert first.numero == 1

    def test_a_conversation_with_only_discarded_orders_has_none(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        orders = OrderRepository(db, tenant_a.tenant_id)
        orders.add_numbered(**_order(tenant_a), estado="descartado")

        assert orders.active_for_conversation(tenant_a.conversation_id) is None


class TestItemsAndProofsArePerTenant:
    def test_a_foreign_orders_items_are_invisible(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        items = OrderItemRepository(db, tenant_a.tenant_id)

        assert items.for_order(seeded["b"].id) == []
        assert [i.nombre for i in items.for_order(seeded["a"].id)] == ["Item a"]
        assert items.count() == 1

    def test_items_of_several_orders_are_grouped_in_one_read(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        items = OrderItemRepository(db, tenant_a.tenant_id)

        grouped = items.for_orders([seeded["a"].id, seeded["b"].id])

        assert [i.nombre for i in grouped[seeded["a"].id]] == ["Item a"]
        # The foreign order id is accepted and simply comes back empty.
        assert grouped[seeded["b"].id] == []
        assert items.for_orders([]) == {}

    def test_a_foreign_item_cannot_be_edited_or_deleted(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        foreign = OrderItemRepository(db, tenant_b.tenant_id).for_order(seeded["b"].id)[0]
        items = OrderItemRepository(db, tenant_a.tenant_id)

        assert items.update(foreign.id, cantidad=999) is None
        assert items.delete(foreign.id) is False

    def test_a_foreign_orders_proofs_are_invisible(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        proofs = PaymentProofRepository(db, tenant_a.tenant_id)

        assert proofs.for_order(seeded["b"].id) == []
        assert proofs.latest_for_order(seeded["b"].id) is None
        latest = proofs.latest_for_order(seeded["a"].id)
        assert latest is not None and latest.codigo_operacion == "OP-a"
        assert proofs.count() == 1

    def test_the_latest_proof_is_the_last_one_written(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        proofs = PaymentProofRepository(db, tenant_a.tenant_id)
        for index in range(3):
            proofs.add(**_proof(tenant_a, order, codigo_operacion=f"OP-{index}"))

        latest = proofs.latest_for_order(order.id)

        assert latest is not None and latest.codigo_operacion == "OP-2"
        assert [p.codigo_operacion for p in proofs.for_order(order.id)] == ["OP-2", "OP-1", "OP-0"]

    def test_a_foreign_proof_cannot_be_reviewed_or_deleted(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        foreign = PaymentProofRepository(db, tenant_b.tenant_id).for_order(seeded["b"].id)[0]
        proofs = PaymentProofRepository(db, tenant_a.tenant_id)

        assert proofs.update(foreign.id, estado="aprobado") is None
        assert proofs.delete(foreign.id) is False
        db.refresh(foreign)
        assert foreign.estado == "por_verificar"

    def test_a_repository_cannot_write_proofs_for_another_tenant(
        self,
        db: Session,
        tenant_a: TenantFixture,
        tenant_b: TenantFixture,
        seeded: dict[str, Order],
    ) -> None:
        with pytest.raises(TenantScopeError):
            PaymentProofRepository(db, tenant_a.tenant_id).add(
                tenant_id=tenant_b.tenant_id, **_proof(tenant_b, seeded["b"])
            )


class TestRulesLiveInTheDatabase:
    def test_a_new_order_starts_as_an_empty_draft(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        db.refresh(order)

        assert order.estado == "borrador"
        assert order.modalidad_pago == "previo"
        assert (order.subtotal, order.descuento, order.envio, order.total) == (Decimal(0),) * 4
        assert order.rechazo_vigente is False
        assert order.is_sandbox is False
        assert order.tipo_entrega is None
        assert order.created_at is not None and order.last_activity_at is not None

    def test_money_comes_back_as_decimal(self, db: Session, tenant_a: TenantFixture) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(
            **_order(tenant_a, subtotal=Decimal("19.99"), total=Decimal("19.99"))
        )
        db.refresh(order)

        assert order.total == Decimal("19.99")
        assert isinstance(order.total, Decimal)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"estado": "pagado"},
            {"modalidad_pago": "fiado"},
            {"tipo_entrega": "dron"},
            {"subtotal": Decimal("-1")},
            {"descuento": Decimal("-1")},
            {"envio": Decimal("-1")},
            {"total": Decimal("-1")},
        ],
    )
    def test_an_invalid_order_is_rejected(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        with pytest.raises(IntegrityError), db.begin_nested():
            OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a, **overrides))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"estado": estado}
            for estado in ("borrador", "por_confirmar", "confirmado", "en_curso", "completado")
        ]
        + [{"modalidad_pago": "contra_entrega"}, {"tipo_entrega": "delivery"}]
        + [{"tipo_entrega": "recojo"}],
    )
    def test_the_documented_values_are_accepted(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(
            **_order(tenant_a, **overrides)
        )

        for field, value in overrides.items():
            assert getattr(order, field) == value

    @pytest.mark.parametrize(
        "overrides",
        [
            {"cantidad": 0},
            {"cantidad": 1000},
            {"precio_unitario": Decimal("-1")},
            {"subtotal": Decimal("-1")},
        ],
    )
    def test_an_invalid_item_is_rejected(
        self, db: Session, tenant_a: TenantFixture, overrides: dict[str, Any]
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))

        with pytest.raises(IntegrityError), db.begin_nested():
            OrderItemRepository(db, tenant_a.tenant_id).add(**_item(order, **overrides))

    @pytest.mark.parametrize("quantity", [1, 999])
    def test_the_quantity_limits_are_accepted(
        self, db: Session, tenant_a: TenantFixture, quantity: int
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))

        item = OrderItemRepository(db, tenant_a.tenant_id).add(**_item(order, cantidad=quantity))

        assert item.cantidad == quantity

    def test_an_item_keeps_its_selections(self, db: Session, tenant_a: TenantFixture) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        selections = [
            {"nombre": "SIAF", "product_id": None},
            {"nombre": "Gestion", "product_id": None},
        ]

        item = OrderItemRepository(db, tenant_a.tenant_id).add(
            **_item(order, bundle_size=5, bundle_selections=selections)
        )
        db.refresh(item)

        assert item.bundle_selections == selections
        default = OrderItemRepository(db, tenant_a.tenant_id).add(**_item(order))
        db.refresh(default)
        assert default.bundle_selections == []

    @pytest.mark.parametrize(
        "overrides",
        [{"estado": "dudoso"}, {"confianza": Decimal("1.01")}, {"confianza": Decimal("-0.01")}],
    )
    def test_an_invalid_proof_is_rejected(
        self,
        db: Session,
        tenant_a: TenantFixture,
        overrides: dict[str, Any],
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))

        with pytest.raises(IntegrityError), db.begin_nested():
            PaymentProofRepository(db, tenant_a.tenant_id).add(
                **_proof(tenant_a, order, **overrides)
            )

    def test_a_new_proof_starts_pending_with_no_observations(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        proof = PaymentProofRepository(db, tenant_a.tenant_id).add(
            **_proof(tenant_a, order, monto=Decimal("500.00"), confianza=Decimal("0.93"))
        )
        db.refresh(proof)

        assert proof.estado == "por_verificar"
        assert proof.observaciones == []
        assert proof.monto == Decimal("500.00")
        assert proof.confianza == Decimal("0.93")


class TestWhatHappensWhenAParentGoes:
    def test_deleting_an_order_deletes_its_items(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        OrderItemRepository(db, tenant_a.tenant_id).add(**_item(order))

        assert OrderRepository(db, tenant_a.tenant_id).delete(order.id) is True
        db.expire_all()

        assert OrderItemRepository(db, tenant_a.tenant_id).count() == 0

    def test_an_order_with_a_proof_cannot_be_deleted(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        """No cascade onto receipts: they are evidence, not a child to tidy up."""
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        PaymentProofRepository(db, tenant_a.tenant_id).add(**_proof(tenant_a, order))

        with pytest.raises(IntegrityError), db.begin_nested():
            OrderRepository(db, tenant_a.tenant_id).delete(order.id)

    def test_deleting_a_product_keeps_what_was_sold(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        item = OrderItemRepository(db, tenant_a.tenant_id).add(
            **_item(order, product_id=tenant_a.product_id)
        )

        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": tenant_a.product_id})
        db.refresh(item)

        assert item.product_id is None
        assert item.nombre == "Paquete 5"
        assert item.precio_unitario == Decimal("500.00")

    def test_deleting_the_message_keeps_the_proof(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        order = OrderRepository(db, tenant_a.tenant_id).add_numbered(**_order(tenant_a))
        proof = PaymentProofRepository(db, tenant_a.tenant_id).add(
            **_proof(tenant_a, order, message_id=tenant_a.message_id)
        )

        db.execute(text("DELETE FROM messages WHERE id = :id"), {"id": tenant_a.message_id})
        db.refresh(proof)

        assert proof.message_id is None
