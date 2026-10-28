"""The order service (Feature 004, TASK-425).

The verification of the task: the full matrix of order transitions, one active
order per conversation (a new one after a confirmed one), per-tenant numbering,
the snapshot of the customer's data on `por_confirmar`, and the rules that keep
money out of the model's hands (prices come from the catalogue, totals from
pricing).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order
from app.models.sales_config import Promotion
from app.models.tenant import Tenant
from app.orders.service import (
    TRANSITIONS,
    BadQuantity,
    InvalidDelivery,
    InvalidTransition,
    LineNotFound,
    NotReady,
    NotSellable,
    OrderError,
    OrderLimit,
    OrderLocked,
    OrderService,
    SelectionRefused,
    customer_snapshot,
)
from app.repositories.base import TenantScopeError
from app.repositories.sales_config import BusinessInfoRepository

D = Decimal
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)

STATES = ("borrador", "por_confirmar", "confirmado", "en_curso", "completado", "descartado")

# `data-model.md` 0019, written out independently of the table in the code.
ALLOWED = {
    ("borrador", "por_confirmar"),
    ("borrador", "descartado"),
    ("por_confirmar", "confirmado"),
    ("por_confirmar", "borrador"),
    ("por_confirmar", "descartado"),
    ("confirmado", "en_curso"),
    ("confirmado", "descartado"),
    ("en_curso", "completado"),
    ("en_curso", "descartado"),
}


@pytest.fixture
def tenant(db: Session) -> Tenant:
    row = Tenant(name=f"Capacita {uuid.uuid4().hex[:6]}")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def catalogue(db: Session, tenant: Tenant) -> dict[str, Product]:
    items = {
        "excel": Product(tenant_id=tenant.id, name="Excel", price=D("100.00"), category="Cursos"),
        "word": Product(tenant_id=tenant.id, name="Word", price=D("80.00"), category="Cursos"),
        "siaf": Product(
            tenant_id=tenant.id, name="SIAF", price=D("120.00"), category="Gestión", es_digital=True
        ),
        "free5": Product(
            tenant_id=tenant.id,
            name="Paquete 5 libres",
            price=D("500.00"),
            bundle_size=5,
            bundle_permite_libres=True,
            es_digital=True,
        ),
        "closed3": Product(
            tenant_id=tenant.id,
            name="Paquete 3 cerrado",
            price=D("240.00"),
            bundle_size=3,
            bundle_permite_libres=False,
        ),
        "quote": Product(
            tenant_id=tenant.id, name="Consultoría", price=D("0.00"), modo_venta="cotizacion"
        ),
    }
    db.add_all(items.values())
    db.flush()
    return items


@pytest.fixture
def contact(db: Session, tenant: Tenant) -> Contact:
    row = Contact(
        tenant_id=tenant.id,
        phone_e164="+51987000111",
        custom_data={"dni": "12345678", "nombres": "Ana"},
        last_inbound_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def conversation(db: Session, tenant: Tenant, contact: Contact) -> Conversation:
    row = Conversation(tenant_id=tenant.id, channel="sandbox", contact_id=contact.id)
    db.add(row)
    db.flush()
    return row


def service(db: Session, tenant: Tenant, **kwargs: Any) -> OrderService:
    kwargs.setdefault("now", NOW)
    return OrderService(db, tenant.id, **kwargs)


def draft(db: Session, tenant: Tenant, conversation: Conversation, **kwargs: Any) -> Order:
    return service(db, tenant, **kwargs).open_draft(
        conversation, moneda="PEN", modalidad_pago="previo", is_sandbox=False
    )


def set_deliveries(db: Session, tenant: Tenant, **overrides: Any) -> None:
    data: dict[str, Any] = {
        "hace_delivery": True,
        "recojo_en_tienda": True,
        "zonas": [
            {"nombre": "Bagua Grande", "costo": "8.00", "tiempo": "24 h"},
            {"nombre": "Jaén", "costo": "15.50", "tiempo": "48 h"},
        ],
        "envio_gratis_desde": None,
        "notas": None,
    }
    data.update(overrides)
    BusinessInfoRepository(db, tenant.id).upsert("entregas", data)


class TestTheTransitionMatrix:
    @pytest.mark.parametrize("source", STATES)
    @pytest.mark.parametrize("target", STATES)
    def test_every_pair_is_allowed_or_refused_as_the_data_model_says(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        source: str,
        target: str,
    ) -> None:
        order = draft(db, tenant, conversation)
        order.estado = source
        db.flush()

        if (source, target) in ALLOWED:
            moved = service(db, tenant).transition(order, target, datos_cliente={"dni": "1"})
            assert moved.estado == target
        else:
            with pytest.raises(InvalidTransition) as raised:
                service(db, tenant).transition(order, target, datos_cliente={"dni": "1"})
            assert (raised.value.source, raised.value.target) == (source, target)
            assert order.estado == source

    def test_the_code_table_matches_the_matrix(self) -> None:
        assert {(s, t) for s, targets in TRANSITIONS.items() for t in targets} == ALLOWED

    def test_finished_orders_go_nowhere(self) -> None:
        assert TRANSITIONS["completado"] == frozenset()
        assert TRANSITIONS["descartado"] == frozenset()

    def test_submitting_needs_the_customers_data(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        order = draft(db, tenant, conversation)

        with pytest.raises(NotReady):
            service(db, tenant).transition(order, "por_confirmar")

        assert order.estado == "borrador"

    def test_confirming_stamps_when_and_by_whom(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        order = draft(db, tenant, conversation)
        order.estado = "por_confirmar"

        service(db, tenant).transition(order, "confirmado", user_id=None)

        assert order.confirmed_at == NOW

    def test_rejecting_goes_back_to_a_draft_with_the_reason(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        order = draft(db, tenant, conversation)
        order.estado = "por_confirmar"

        service(db, tenant).transition(
            order, "borrador", motivo="El monto no coincide", rechazo_vigente=True
        )

        assert (order.estado, order.motivo_rechazo, order.rechazo_vigente) == (
            "borrador",
            "El monto no coincide",
            True,
        )

    def test_rejecting_cash_on_delivery_leaves_no_proof_rejection_in_force(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        order = draft(db, tenant, conversation)
        order.estado = "por_confirmar"

        service(db, tenant).transition(order, "borrador", motivo="Dirección incompleta")

        assert order.rechazo_vigente is False


class TestTheActiveOrder:
    def test_the_first_call_creates_a_draft_and_the_next_returns_it(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        first = draft(db, tenant, conversation)
        again = draft(db, tenant, conversation)

        assert first.id == again.id
        assert first.estado == "borrador"
        assert first.moneda == "PEN"
        assert first.contact_id == conversation.contact_id
        assert service(db, tenant).active_for(conversation.id).id == first.id  # type: ignore[union-attr]

    def test_after_a_confirmed_order_a_new_one_is_started(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        first = draft(db, tenant, conversation)
        first.estado = "confirmado"
        db.flush()

        second = draft(db, tenant, conversation)

        assert second.id != first.id
        assert second.estado == "borrador"
        assert second.numero == first.numero + 1
        assert service(db, tenant).active_for(conversation.id).id == second.id  # type: ignore[union-attr]

    @pytest.mark.parametrize("finished", ["confirmado", "en_curso", "completado"])
    def test_every_sale_made_is_closed_business(
        self, db: Session, tenant: Tenant, conversation: Conversation, finished: str
    ) -> None:
        first = draft(db, tenant, conversation)
        first.estado = finished
        db.flush()

        assert draft(db, tenant, conversation).id != first.id

    def test_a_discarded_order_is_not_the_active_one(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        first = draft(db, tenant, conversation)
        first.estado = "descartado"
        db.flush()

        assert service(db, tenant).active_for(conversation.id) is None
        assert draft(db, tenant, conversation).id != first.id

    def test_an_order_waiting_for_the_operator_cannot_be_replaced_or_edited(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        order.estado = "por_confirmar"
        db.flush()

        with pytest.raises(OrderLocked):
            draft(db, tenant, conversation)
        with pytest.raises(OrderLocked):
            service(db, tenant).add_product(order, catalogue["excel"])

    def test_a_conversation_without_a_contact_cannot_order(
        self, db: Session, tenant: Tenant
    ) -> None:
        lonely = Conversation(tenant_id=tenant.id, channel="sandbox")
        db.add(lonely)
        db.flush()

        with pytest.raises(OrderError):
            draft(db, tenant, lonely)

    def test_the_sandbox_flag_and_payment_mode_are_copied_to_the_order(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        order = service(db, tenant).open_draft(
            conversation, moneda="usd", modalidad_pago="contra_entrega", is_sandbox=True
        )

        assert (order.is_sandbox, order.modalidad_pago, order.moneda) == (
            True,
            "contra_entrega",
            "USD",
        )

    def test_numbers_are_consecutive_per_tenant(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        numbers = []
        for _ in range(3):
            order = draft(db, tenant, conversation)
            numbers.append(order.numero)
            order.estado = "descartado"
            db.flush()

        assert numbers == [1, 2, 3]


class TestLines:
    def test_the_price_comes_from_the_catalogue(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)

        item = service(db, tenant).add_product(order, catalogue["excel"], 2)

        assert (item.nombre, item.precio_unitario, item.cantidad) == ("Excel", D("100.00"), 2)
        assert item.subtotal == D("200.00")
        assert (order.subtotal, order.total) == (D("200.00"), D("200.00"))

    def test_the_same_product_twice_is_one_line(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"], 1)
        svc.add_product(order, catalogue["excel"], 2)

        lines = svc.lines(order)

        assert [(line.nombre, line.cantidad) for line in lines] == [("Excel", 3)]
        assert order.total == D("300.00")

    def test_a_later_price_change_does_not_rewrite_the_line(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"], 1)
        catalogue["excel"].price = D("999.00")
        db.flush()

        svc.add_product(order, catalogue["word"], 1)

        assert order.total == D("180.00")

    def test_a_quoted_product_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)

        with pytest.raises(NotSellable):
            service(db, tenant).add_product(order, catalogue["quote"])

        assert service(db, tenant).lines(order) == []

    def test_a_product_of_another_tenant_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
    ) -> None:
        stranger = Tenant(name="Otro")
        db.add(stranger)
        db.flush()
        alien = Product(tenant_id=stranger.id, name="Ajeno", price=D("1.00"))
        db.add(alien)
        db.flush()

        with pytest.raises(TenantScopeError):
            service(db, tenant).add_product(draft(db, tenant, conversation), alien)

    @pytest.mark.parametrize("quantity", [0, -1, 1000])
    def test_a_quantity_outside_one_to_999_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
        quantity: int,
    ) -> None:
        with pytest.raises(BadQuantity):
            service(db, tenant).add_product(
                draft(db, tenant, conversation), catalogue["excel"], quantity
            )

    def test_the_sum_of_two_adds_may_not_pass_999(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"], 999)

        with pytest.raises(BadQuantity):
            svc.add_product(order, catalogue["excel"], 1)

    def test_the_template_may_limit_the_lines(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"], max_lines=2)
        svc.add_product(order, catalogue["word"], max_lines=2)

        with pytest.raises(OrderLimit) as raised:
            svc.add_product(order, catalogue["siaf"], max_lines=2)
        assert raised.value.limit == 2
        # More of a line it already has is not a new line.
        svc.add_product(order, catalogue["word"], max_lines=2)

    def test_changing_a_quantity_removing_and_clearing(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"], 1)
        svc.add_product(order, catalogue["word"], 1)

        svc.set_quantity(order, catalogue["excel"].id, 4)
        assert order.total == D("480.00")

        svc.remove_product(order, catalogue["word"].id)
        assert [line.nombre for line in svc.lines(order)] == ["Excel"]
        assert order.total == D("400.00")

        svc.clear(order)
        assert svc.lines(order) == []
        assert (order.subtotal, order.total, order.estado) == (D("0.00"), D("0.00"), "borrador")

    def test_a_product_that_is_not_in_the_order(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)

        with pytest.raises(LineNotFound):
            service(db, tenant).remove_product(order, catalogue["excel"].id)
        with pytest.raises(LineNotFound):
            service(db, tenant).set_quantity(order, catalogue["excel"].id, 2)

    def test_only_a_draft_can_be_edited(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        service(db, tenant).add_product(order, catalogue["excel"])
        order.estado = "confirmado"

        with pytest.raises(OrderLocked):
            service(db, tenant).set_quantity(order, catalogue["excel"].id, 2)
        with pytest.raises(OrderLocked):
            service(db, tenant).clear(order)

    def test_any_change_turns_the_rejection_off(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"])
        for change in (
            lambda: svc.add_product(order, catalogue["word"]),
            lambda: svc.set_quantity(order, catalogue["word"].id, 2),
            lambda: svc.remove_product(order, catalogue["word"].id),
            lambda: svc.clear(order),
        ):
            order.rechazo_vigente = True
            change()
            assert order.rechazo_vigente is False

    def test_changing_customer_data_turns_it_off_too(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        order = draft(db, tenant, conversation)
        order.rechazo_vigente = True

        service(db, tenant).customer_data_changed(order)

        assert order.rechazo_vigente is False

    def test_the_order_is_alive_after_a_change(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        order.last_activity_at = NOW - timedelta(days=3)

        service(db, tenant).add_product(order, catalogue["excel"])

        assert order.last_activity_at == NOW


class TestPackages:
    def package(
        self, db: Session, tenant: Tenant, conversation: Conversation, product: Product
    ) -> tuple[Order, OrderService]:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, product)
        return order, svc

    def test_a_package_of_five_with_two_selections_is_not_complete(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        svc.add_selection(order, "SIAF")
        svc.add_selection(order, "Gestión pública")

        assert svc.missing_selections(order) == 3
        assert svc.is_complete(order) is False

    def test_a_package_with_all_its_selections_is_complete(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        for name in ("SIAF", "Gestión pública", "Excel avanzado", "Power BI", "SEACE"):
            svc.add_selection(order, name)

        assert svc.missing_selections(order) == 0
        assert svc.is_complete(order) is True

    def test_an_empty_order_is_not_complete(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        assert service(db, tenant).is_complete(draft(db, tenant, conversation)) is False

    def test_a_package_that_allows_free_themes_takes_any_name(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])

        stored = svc.add_selection(order, "  Excel   avanzado ")

        assert stored == {"nombre": "Excel avanzado", "product_id": None}

    def test_a_free_name_that_is_a_catalogue_product_keeps_its_id(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])

        stored = svc.add_selection(order, "siaf")

        assert stored == {"nombre": "SIAF", "product_id": str(catalogue["siaf"].id)}

    def test_a_closed_package_takes_only_catalogue_products(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["closed3"])

        assert svc.add_selection(order, "Excel")["product_id"] == str(catalogue["excel"].id)
        with pytest.raises(SelectionRefused) as raised:
            svc.add_selection(order, "Tema inventado")

        assert raised.value.code == "not_in_catalog"
        assert "Excel" in raised.value.suggestions
        # Packages are not offered as the content of a package.
        assert "Paquete 5 libres" not in raised.value.suggestions

    def test_a_package_is_not_a_selection_of_another_package(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["closed3"])

        with pytest.raises(SelectionRefused):
            svc.add_selection(order, "Paquete 5 libres")

    def test_a_selection_cannot_repeat_or_overflow(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["closed3"])
        svc.add_selection(order, "Excel")
        with pytest.raises(SelectionRefused) as repeated:
            svc.add_selection(order, "excel")
        assert repeated.value.code == "duplicate"

        svc.add_selection(order, "Word")
        svc.add_selection(order, "SIAF")
        with pytest.raises(SelectionRefused) as overflow:
            svc.add_selection(order, "Excel avanzado")
        assert overflow.value.code == "full"

    def test_an_empty_selection_is_refused(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])

        with pytest.raises(SelectionRefused) as raised:
            svc.add_selection(order, "   ")
        assert raised.value.code == "empty"

    def test_without_a_package_there_is_nothing_to_choose_for(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["excel"])

        with pytest.raises(SelectionRefused) as raised:
            svc.add_selection(order, "SIAF")
        assert raised.value.code == "no_package"

    def test_the_selection_goes_to_the_package_asked_for(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        svc.add_product(order, catalogue["closed3"])

        svc.add_selection(order, "Excel", package_id=catalogue["closed3"].id)

        by_name = {line.nombre: line for line in svc.lines(order)}
        assert by_name["Paquete 3 cerrado"].bundle_selections[0]["nombre"] == "Excel"
        assert by_name["Paquete 5 libres"].bundle_selections == []

    def test_it_goes_to_the_first_package_that_is_still_incomplete(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["closed3"])
        # The suite pins the clock, so the second line needs a later stamp to be "second".
        later = service(db, tenant, now=NOW + timedelta(seconds=1))
        later.add_product(order, catalogue["free5"])
        for name in ("Excel", "Word", "SIAF"):
            svc.add_selection(order, name)

        svc.add_selection(order, "Tema libre")

        by_name = {line.nombre: line for line in svc.lines(order)}
        assert by_name["Paquete 5 libres"].bundle_selections[0]["nombre"] == "Tema libre"

    def test_two_packages_need_twice_the_selections(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        svc.set_quantity(order, catalogue["free5"].id, 2)

        assert svc.missing_selections(order) == 10

    def test_lowering_the_quantity_drops_the_selections_that_no_longer_fit(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["closed3"])
        svc.set_quantity(order, catalogue["closed3"].id, 2)
        for name in ("Excel", "Word", "SIAF"):
            svc.add_selection(order, name)

        svc.set_quantity(order, catalogue["closed3"].id, 1)

        assert svc.missing_selections(order) == 0
        assert len(svc.lines(order)[0].bundle_selections) == 3

    def test_a_selection_can_be_taken_back(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        svc.add_selection(order, "SIAF")
        svc.add_selection(order, "Power BI")

        svc.remove_selection(order, "power bi")

        assert [s["nombre"] for s in svc.lines(order)[0].bundle_selections] == ["SIAF"]
        with pytest.raises(SelectionRefused) as raised:
            svc.remove_selection(order, "Power BI")
        assert raised.value.code == "not_chosen"

    def test_removing_a_selection_from_a_named_package(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        svc.add_selection(order, "SIAF")

        svc.remove_selection(order, "SIAF", package_id=catalogue["free5"].id)

        assert svc.lines(order)[0].bundle_selections == []
        with pytest.raises(SelectionRefused):
            svc.remove_selection(order, "SIAF", package_id=catalogue["closed3"].id)

    def test_selections_change_the_rejection(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.package(db, tenant, conversation, catalogue["free5"])
        order.rechazo_vigente = True

        svc.add_selection(order, "SIAF")

        assert order.rechazo_vigente is False


class TestPromotionsOnTheOrder:
    def promo(self, db: Session, tenant: Tenant, **values: Any) -> Promotion:
        base: dict[str, Any] = {
            "tenant_id": tenant.id,
            "nombre": "10 % en todo",
            "uso": "automatica",
            "tipo": "porcentaje",
            "valor": D("10"),
            "aplica_a": "todo",
        }
        base.update(values)
        row = Promotion(**base)
        db.add(row)
        db.flush()
        return row

    def test_an_automatic_promotion_applies_by_itself(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        promo = self.promo(db, tenant)
        order = draft(db, tenant, conversation)

        service(db, tenant).add_product(order, catalogue["excel"], 2)

        assert (order.subtotal, order.descuento, order.total) == (
            D("200.00"),
            D("20.00"),
            D("180.00"),
        )
        assert order.promotion_id == promo.id
        assert order.promotion_snapshot is not None
        assert order.promotion_snapshot["nombre"] == "10 % en todo"

    def test_a_closing_promotion_waits_for_the_offer(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        promo = self.promo(
            db, tenant, nombre="Cupón", uso="cierre", valor=D("20"), vigencia_horas=24
        )
        order = draft(db, tenant, conversation)
        service(db, tenant).add_product(order, catalogue["excel"], 1)
        assert order.descuento == D("0.00")

        offered = {str(promo.id): NOW + timedelta(hours=3)}
        service(db, tenant, offers=offered).recalculate(order)

        assert (order.descuento, order.total) == (D("20.00"), D("80.00"))
        assert order.promotion_id == promo.id

    def test_the_offer_applies_to_the_order_created_within_its_validity_but_not_after(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        promo = self.promo(db, tenant, uso="cierre", valor=D("20"), vigencia_horas=24)
        offered = {str(promo.id): (NOW + timedelta(hours=1)).isoformat()}

        inside = draft(db, tenant, conversation, offers=offered)
        service(db, tenant, offers=offered).add_product(inside, catalogue["excel"])
        assert inside.descuento == D("20.00")

        later = NOW + timedelta(hours=2)
        service(db, tenant, offers=offered, now=later).recalculate(inside)
        assert (inside.descuento, inside.promotion_id) == (D("0.00"), None)
        assert inside.promotion_snapshot is None

    def test_the_best_promotion_wins_and_they_do_not_add_up(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        self.promo(db, tenant, nombre="5 %", valor=D("5"))
        big = self.promo(db, tenant, nombre="Monto", tipo="monto_fijo", valor=D("30"))
        order = draft(db, tenant, conversation)

        service(db, tenant).add_product(order, catalogue["excel"], 2)

        assert order.descuento == D("30.00")
        assert order.promotion_id == big.id

    def test_a_promotion_by_category_reads_the_catalogue(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        self.promo(db, tenant, aplica_a="categorias", categorias=["Gestión"], valor=D("50"))
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)

        svc.add_product(order, catalogue["excel"])
        assert order.descuento == D("0.00")
        svc.add_product(order, catalogue["siaf"])
        assert order.descuento == D("60.00")

    def test_a_promotion_outside_its_dates_is_not_used(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        self.promo(db, tenant, termina_en=NOW - timedelta(days=1))
        order = draft(db, tenant, conversation)

        service(db, tenant).add_product(order, catalogue["excel"])

        assert order.descuento == D("0.00")


class TestDelivery:
    def ready(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
        **entregas: Any,
    ) -> tuple[Order, OrderService]:
        set_deliveries(db, tenant, **entregas)
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"])
        return order, svc

    def test_a_zone_adds_its_cost(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue)

        svc.set_delivery(order, tipo_entrega="delivery", zona="jaén")

        assert (order.tipo_entrega, order.zona_envio) == ("delivery", "Jaén")
        assert (order.envio, order.total) == (D("15.50"), D("115.50"))

    def test_an_unknown_zone_lists_the_valid_ones(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue)

        with pytest.raises(InvalidDelivery) as raised:
            svc.set_delivery(order, tipo_entrega="delivery", zona="Marte")

        assert raised.value.valid_zones == ("Bagua Grande", "Jaén")

    def test_pickup_clears_the_zone_and_the_cost(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue)
        svc.set_delivery(order, tipo_entrega="delivery", zona="Jaén")

        svc.set_delivery(order, tipo_entrega="recojo")

        assert (order.tipo_entrega, order.zona_envio, order.envio) == ("recojo", None, D("0.00"))

    def test_free_shipping_from_an_amount(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue, envio_gratis_desde="100.00")

        svc.set_delivery(order, tipo_entrega="delivery", zona="Jaén")

        assert (order.envio, order.total) == (D("0.00"), D("100.00"))

    def test_changing_the_items_reprices_the_shipping(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue, envio_gratis_desde="150.00")
        svc.set_delivery(order, tipo_entrega="delivery", zona="Jaén")
        assert order.envio == D("15.50")

        svc.add_product(order, catalogue["word"])

        assert (order.envio, order.total) == (D("0.00"), D("180.00"))

    def test_a_business_without_zones_arranges_the_cost(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue, zonas=[])

        svc.set_delivery(order, tipo_entrega="delivery", zona="Jaén")

        assert (order.zona_envio, order.envio) == (None, D("0.00"))

    def test_a_business_that_only_picks_up_refuses_delivery(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue, hace_delivery=False, zonas=[])

        with pytest.raises(InvalidDelivery):
            svc.set_delivery(order, tipo_entrega="delivery")
        svc.set_delivery(order, tipo_entrega="recojo")
        assert order.tipo_entrega == "recojo"

    def test_a_business_that_only_delivers_refuses_pickup(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue, recojo_en_tienda=False)

        with pytest.raises(InvalidDelivery):
            svc.set_delivery(order, tipo_entrega="recojo")

    def test_without_the_section_there_is_no_delivery(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        service(db, tenant).add_product(order, catalogue["excel"])

        with pytest.raises(InvalidDelivery):
            service(db, tenant).set_delivery(order, tipo_entrega="delivery")
        assert service(db, tenant).requires_delivery(order) is False

    def test_an_invalid_section_counts_as_missing(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        BusinessInfoRepository(db, tenant.id).upsert("entregas", {"basura": True})

        assert service(db, tenant).entregas() is None

    def test_a_physical_product_requires_delivery_data_and_a_digital_one_does_not(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        svc = service(db, tenant)
        digital = draft(db, tenant, conversation)
        svc.add_product(digital, catalogue["siaf"])
        assert svc.requires_delivery(digital) is False

        svc.add_product(digital, catalogue["excel"])
        assert svc.requires_delivery(digital) is True

    def test_a_line_whose_product_was_removed_counts_as_physical(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        set_deliveries(db, tenant)
        svc = service(db, tenant)
        order = draft(db, tenant, conversation)
        svc.add_product(order, catalogue["siaf"])
        svc.lines(order)[0].product_id = None
        db.flush()

        assert svc.requires_delivery(order) is True

    def test_delivery_change_ends_a_rejection(
        self,
        db: Session,
        tenant: Tenant,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order, svc = self.ready(db, tenant, conversation, catalogue)
        order.rechazo_vigente = True

        svc.set_delivery(order, tipo_entrega="recojo")

        assert order.rechazo_vigente is False


class TestSubmitting:
    def test_the_customers_data_are_copied_at_that_moment(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, catalogue["excel"])

        svc.submit(order, contact)
        contact.custom_data = {**contact.custom_data, "dni": "99999999"}
        db.flush()

        assert order.estado == "por_confirmar"
        assert order.datos_cliente == {
            "dni": "12345678",
            "nombres": "Ana",
            "telefono": "+51987000111",
        }

    def test_an_incomplete_order_cannot_be_submitted(
        self,
        db: Session,
        tenant: Tenant,
        contact: Contact,
        conversation: Conversation,
        catalogue: dict[str, Product],
    ) -> None:
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        with pytest.raises(NotReady):
            svc.submit(order, contact)

        svc.add_product(order, catalogue["free5"])
        with pytest.raises(NotReady):
            svc.submit(order, contact)
        assert order.estado == "borrador"

    def test_the_snapshot_keeps_the_phone_the_customer_told_us(self) -> None:
        told = Contact(phone_e164="+51900000000", custom_data={"telefono": "+51911111111"})
        silent = Contact(phone_e164=None, custom_data={})

        assert customer_snapshot(told)["telefono"] == "+51911111111"
        assert customer_snapshot(silent) == {}


class TestAPackageThatChoosesFromOneCategory:
    """Feature 020 (D2): «paquete de 3 herramientas» admits the hammer, not the paint."""

    @staticmethod
    def _hardware(db: Session, tenant: Tenant, *, free: bool = False) -> Product:
        for name, category in (
            ("Martillo", "Herramientas"),
            ("Taladro", "Herramientas"),
            ("Escuadra", "Herramientas"),
            ("Pintura látex", "Pinturas"),
        ):
            db.add(Product(tenant_id=tenant.id, name=name, price=Decimal("30"), category=category))
        kit = Product(
            tenant_id=tenant.id,
            name="Kit de 3 herramientas",
            price=Decimal("80"),
            bundle_size=3,
            bundle_categoria="herramientas",
            bundle_permite_libres=free,
        )
        db.add(kit)
        db.flush()
        return kit

    def test_an_item_of_the_category_is_taken(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        kit = self._hardware(db, tenant)
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, kit)

        assert svc.add_selection(order, "martillo")["nombre"] == "Martillo"

    def test_an_item_of_another_category_is_refused_with_the_category_s_options(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        kit = self._hardware(db, tenant)
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, kit)

        with pytest.raises(SelectionRefused) as raised:
            svc.add_selection(order, "Pintura látex")

        assert raised.value.code == "not_in_catalog"
        assert set(raised.value.suggestions) == {"Escuadra", "Martillo", "Taladro"}

    def test_with_free_names_a_new_name_is_taken_but_not_another_category_s_item(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        kit = self._hardware(db, tenant, free=True)
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, kit)

        assert svc.add_selection(order, "Sierra de mano") == {
            "nombre": "Sierra de mano",
            "product_id": None,
        }
        with pytest.raises(SelectionRefused):
            svc.add_selection(order, "Pintura látex")

    def test_a_short_name_takes_the_one_item_it_names(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        # T2.7 (simulation): the customer says «martillo, wincha y alicate», never the full names.
        kit = self._hardware(db, tenant)
        db.add(
            Product(
                tenant_id=tenant.id,
                name="Wincha 5 m",
                price=Decimal("18"),
                category="Herramientas",
            )
        )
        db.flush()
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, kit)

        assert svc.add_selection(order, "wincha")["nombre"] == "Wincha 5 m"
        assert svc.add_selection(order, "taladros")["nombre"] == "Taladro"

    def test_a_name_of_two_items_is_refused_offering_those_two_first(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        kit = self._hardware(db, tenant)
        db.add(
            Product(
                tenant_id=tenant.id,
                name="Martillo de goma",
                price=Decimal("25"),
                category="Herramientas",
            )
        )
        db.flush()
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, kit)

        with pytest.raises(SelectionRefused) as raised:
            svc.add_selection(order, "martillo grande")

        assert list(raised.value.suggestions[:2]) == ["Martillo", "Martillo de goma"]

    def test_with_free_names_a_short_name_is_kept_as_the_customer_wrote_it(
        self, db: Session, tenant: Tenant, conversation: Conversation
    ) -> None:
        kit = self._hardware(db, tenant, free=True)
        order = draft(db, tenant, conversation)
        svc = service(db, tenant)
        svc.add_product(order, kit)

        assert svc.add_selection(order, "taladro percutor") == {
            "nombre": "taladro percutor",
            "product_id": None,
        }
