"""The facts model and the rules that derive facts from rows
(Feature 004, TASK-411).

`build_facts` is pure: it takes the rows a conversation is made of and returns
`Facts`. So the rules - what is still missing from the customer's data, when a
payment counts as rejected, when the payment methods count as sent - are checked
here with plain objects, and `test_facts_loader_isolation.py` checks the same
rules once through the real database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.models.channel import Contact
from app.models.conversation_state import ConversationState
from app.models.orders import Order, OrderItem, PaymentProof
from app.models.sales_config import Promotion
from app.process.facts import (
    STORED_KEYS,
    Facts,
    FactsConfig,
    build_facts,
    payment_state,
)
from app.process.templates import TEMPLATES
from app.repositories.conversation_snapshot import ConversationSnapshot, OrderLine
from app.schemas.business_info import Entregas, Zona

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
SHOP = TEMPLATES["productos"].resolve()
CERT = TEMPLATES["certificaciones"].resolve()


def _contact(**overrides: Any) -> Contact:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "phone_e164": "+51987654321",
        "profile_name": None,
        "custom_data": {},
    }
    values.update(overrides)
    return Contact(**values)


def _order(**overrides: Any) -> Order:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "numero": 42,
        "estado": "borrador",
        "modalidad_pago": "previo",
        "moneda": "PEN",
        "total": Decimal("500.00"),
        "rechazo_vigente": False,
        "motivo_rechazo": None,
        "tipo_entrega": None,
        "zona_envio": None,
    }
    values.update(overrides)
    return Order(**values)


def _item(**overrides: Any) -> OrderItem:
    values: dict[str, Any] = {
        "nombre": "Paquete 5",
        "cantidad": 1,
        "bundle_size": None,
        "bundle_selections": [],
    }
    values.update(overrides)
    return OrderItem(**values)


def _proof(estado: str = "por_verificar", **overrides: Any) -> PaymentProof:
    values: dict[str, Any] = {"estado": estado, "observaciones": []}
    values.update(overrides)
    return PaymentProof(**values)


def _promo(**overrides: Any) -> Promotion:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "nombre": "Cupón 20 %",
        "uso": "cierre",
        "vigencia_horas": 24,
    }
    values.update(overrides)
    return Promotion(**values)


def _state(facts: dict[str, Any]) -> ConversationState:
    return ConversationState(facts=facts)


def _entregas(**overrides: Any) -> Entregas:
    values: dict[str, Any] = {"hace_delivery": True, "recojo_en_tienda": False}
    values.update(overrides)
    return Entregas(**values)


def _snapshot(
    *,
    order: Order | None = None,
    lines: list[tuple[OrderItem, bool]] | None = None,
    **fields: Any,
) -> ConversationSnapshot:
    return ConversationSnapshot(
        order=order,
        lines=[OrderLine(item=item, es_digital=digital) for item, digital in (lines or [])],
        **fields,
    )


def _build(snapshot: ConversationSnapshot, **config: Any) -> Facts:
    config.setdefault("options", SHOP)
    return build_facts(snapshot, FactsConfig(**config), NOW)


class TestTheModel:
    def test_a_conversation_without_state_starts_empty(self) -> None:
        facts = Facts()

        assert facts.saludo_enviado is False
        assert facts.pedido is None
        assert facts.derivado_activo(NOW) is False

    def test_unknown_keys_do_not_break_reading(self) -> None:
        """Facts are read from JSON written by older code."""
        facts = Facts.from_stored({"saludo_enviado": True, "clave_vieja": 1})

        assert facts.saludo_enviado is True

    def test_stored_round_trip(self) -> None:
        order_id = uuid.uuid4()
        stored: dict[str, Any] = {
            "saludo_enviado": True,
            "camino": "certificacion",
            "vio_productos": True,
            "recursos_enviados": ["saludo", "flyer_paquetes"],
            "opciones_mostradas": ["caminos"],
            "metodos_pago_enviados": {"order_id": str(order_id), "en": "2026-10-10T16:10:00Z"},
            "derivado": {
                "motivo": "pide_humano",
                "resumen": "Quiere hablar",
                "hasta": "2026-10-10T21:00:00Z",
            },
            "asesoria_derivada_en": "2026-10-10T15:20:00Z",
            "seguimiento": {"intentos": 1, "ultimo_en": "2026-10-10T19:00:00Z"},
            "promos_ofrecidas": {"abc": "2026-10-11T19:00:00Z"},
            "entrada_anuncio": {"ad_id": "1", "recibido_en": "2026-10-10T15:02:00Z"},
        }

        facts = Facts.from_stored(stored)
        again = Facts.from_stored(facts.to_stored())

        assert again == facts
        assert facts.metodos_pago_registro is not None
        assert facts.metodos_pago_registro.order_id == order_id
        assert facts.to_stored()["metodos_pago_enviados"]["order_id"] == str(order_id)

    def test_derived_facts_are_never_written_back(self) -> None:
        facts = Facts(
            saludo_enviado=True,
            nombre_cliente="Ana",
            datos_faltantes=["dni"],
            pago_estado="rechazado",
            metodos_pago_enviados=True,
            promos_cierre=[],
        )

        stored = facts.to_stored()

        assert set(stored) <= STORED_KEYS
        assert "pago_estado" not in stored
        assert "metodos_pago_enviados" not in stored  # the derived flag, not the record

    def test_a_derived_key_found_in_storage_is_ignored(self) -> None:
        """A stale `pago_estado` in the JSON must not override the real one."""
        facts = Facts.from_stored({"pago_estado": "confirmado", "datos_completos": True})

        assert facts.pago_estado is None
        assert facts.datos_completos is False

    def test_a_derived_value_wins_over_nothing_stored(self) -> None:
        facts = Facts.from_stored({}, pago_estado="rechazado")

        assert facts.pago_estado == "rechazado"

    def test_the_stored_facts_of_the_data_model_are_all_known(self) -> None:
        assert {
            "saludo_enviado",
            "entrada_anuncio",
            "camino",
            "paquete_pedido",
            "vio_productos",
            "recursos_enviados",
            "opciones_mostradas",
            "metodos_pago_enviados",
            "derivado",
            "asesoria_derivada_en",
            "seguimiento",
            "promos_ofrecidas",
            "cita_agendada",
            "lead_calificado",
        } == STORED_KEYS

    def test_an_empty_facts_object_serialises_to_the_minimum(self) -> None:
        assert Facts().to_stored() == {
            "saludo_enviado": False,
            "vio_productos": False,
            "recursos_enviados": [],
            "opciones_mostradas": [],
            "promos_ofrecidas": {},
        }


class TestPaymentState:
    """The table of `data-model.md` 0019."""

    @pytest.mark.parametrize("estado", ["confirmado", "en_curso", "completado"])
    def test_a_confirmed_order_is_confirmed(self, estado: str) -> None:
        assert payment_state(_order(estado=estado), None) == "confirmado"

    def test_an_order_waiting_for_the_operator_is_under_review_with_or_without_proof(self) -> None:
        order = _order(estado="por_confirmar")

        assert payment_state(order, None) == "por_verificar"
        assert payment_state(order, _proof("por_verificar")) == "por_verificar"
        assert payment_state(order, _proof("rechazado")) == "por_verificar"

    def test_a_draft_with_a_live_rejection_is_rejected(self) -> None:
        order = _order(rechazo_vigente=True)

        assert payment_state(order, _proof("rechazado")) == "rechazado"

    def test_a_rejection_that_was_switched_off_is_not_rejected(self) -> None:
        """The customer changed the order or their data (C-21): they leave VERIFICACION."""
        order = _order(rechazo_vigente=False)

        assert payment_state(order, _proof("rechazado")) is None

    def test_a_live_rejection_needs_a_rejected_proof(self) -> None:
        order = _order(rechazo_vigente=True)

        assert payment_state(order, None) is None
        assert payment_state(order, _proof("por_verificar")) is None

    def test_a_plain_draft_has_no_payment_state(self) -> None:
        assert payment_state(_order(), None) is None

    def test_no_order_no_payment_state(self) -> None:
        assert payment_state(None, _proof("rechazado")) is None

    def test_a_discarded_order_has_none(self) -> None:
        assert payment_state(_order(estado="descartado"), None) is None


class TestTheOrder:
    def test_no_order_means_no_pedido(self) -> None:
        assert _build(_snapshot()).pedido is None

    def test_a_single_product_order_is_complete(self) -> None:
        facts = _build(_snapshot(order=_order(), lines=[(_item(), False)]))

        assert facts.pedido is not None
        assert facts.pedido.completo is True
        assert facts.pedido.faltan_selecciones == 0
        assert facts.pedido.numero == 42
        assert facts.pedido.total == Decimal("500.00")
        assert facts.pedido.items[0].nombre == "Paquete 5"

    def test_an_order_without_lines_is_not_complete(self) -> None:
        facts = _build(_snapshot(order=_order()))

        assert facts.pedido is not None
        assert facts.pedido.completo is False
        assert facts.pedido.items == []

    def test_a_package_with_two_of_five_selections_is_incomplete(self) -> None:
        item = _item(
            bundle_size=5,
            bundle_selections=[{"nombre": "SIAF"}, {"nombre": "Gestión pública"}],
        )

        facts = _build(_snapshot(order=_order(), lines=[(item, True)]))

        assert facts.pedido is not None
        assert facts.pedido.completo is False
        assert facts.pedido.faltan_selecciones == 3
        assert facts.pedido.items[0].selecciones == ["SIAF", "Gestión pública"]

    def test_a_full_package_is_complete(self) -> None:
        item = _item(bundle_size=2, bundle_selections=[{"nombre": "A"}, {"nombre": "B"}])

        facts = _build(_snapshot(order=_order(), lines=[(item, True)]))

        assert facts.pedido is not None and facts.pedido.completo is True

    def test_two_packages_need_twice_the_selections(self) -> None:
        item = _item(cantidad=2, bundle_size=3, bundle_selections=[{"nombre": "A"}] * 4)

        facts = _build(_snapshot(order=_order(), lines=[(item, True)]))

        assert facts.pedido is not None
        assert facts.pedido.faltan_selecciones == 2
        assert facts.pedido.completo is False

    def test_extra_selections_do_not_make_the_count_negative(self) -> None:
        item = _item(bundle_size=2, bundle_selections=[{"nombre": "A"}] * 3)

        facts = _build(_snapshot(order=_order(), lines=[(item, True)]))

        assert facts.pedido is not None
        assert facts.pedido.faltan_selecciones == 0

    def test_one_incomplete_package_makes_the_whole_order_incomplete(self) -> None:
        full = _item(nombre="Completo", bundle_size=1, bundle_selections=[{"nombre": "A"}])
        partial = _item(nombre="Parcial", bundle_size=2, bundle_selections=[{"nombre": "A"}])
        plain = _item(nombre="Suelto")

        facts = _build(
            _snapshot(order=_order(), lines=[(full, True), (partial, True), (plain, False)])
        )

        assert facts.pedido is not None
        assert facts.pedido.completo is False
        assert facts.pedido.faltan_selecciones == 1

    def test_the_order_state_and_modality_come_from_the_order(self) -> None:
        order = _order(estado="por_confirmar", modalidad_pago="contra_entrega")

        facts = _build(_snapshot(order=order, lines=[(_item(), False)]))

        assert facts.pedido is not None and facts.pedido.estado == "por_confirmar"
        assert facts.modalidad_pago == "contra_entrega"

    def test_without_an_order_the_modality_is_the_templates(self) -> None:
        cash_on_delivery = TEMPLATES["productos"].resolve(
            TEMPLATES["productos"].validate_options({"modalidad_pago": "contra_entrega"})
        )

        facts = _build(_snapshot(), options=cash_on_delivery)

        assert facts.modalidad_pago == "contra_entrega"


class TestMissingCustomerData:
    def test_everything_is_missing_for_a_new_customer(self) -> None:
        facts = _build(_snapshot(contact=_contact(phone_e164=None)), options=CERT)

        assert facts.datos_faltantes == ["dni", "nombres", "apellidos", "correo", "telefono"]
        assert facts.datos_completos is False

    def test_the_phone_they_write_from_counts(self) -> None:
        facts = _build(_snapshot(contact=_contact(custom_data={"nombres": "Ana"})))

        assert facts.datos_faltantes == []
        assert facts.datos_completos is True

    def test_a_contact_with_only_a_bsuid_is_asked_for_the_phone(self) -> None:
        contact = _contact(
            phone_e164=None, external_user_id="BSUID", custom_data={"nombres": "Ana"}
        )

        facts = _build(_snapshot(contact=contact))

        assert facts.datos_faltantes == ["telefono"]

    def test_partial_data_asks_only_for_the_rest(self) -> None:
        contact = _contact(custom_data={"dni": "12345678", "nombres": "Ana"})

        facts = _build(_snapshot(contact=contact), options=CERT)

        assert facts.datos_faltantes == ["apellidos", "correo"]

    def test_a_blank_value_counts_as_missing(self) -> None:
        contact = _contact(custom_data={"nombres": "   ", "dni": None})

        facts = _build(_snapshot(contact=contact), options=CERT)

        assert "nombres" in facts.datos_faltantes
        assert "dni" in facts.datos_faltantes

    def test_a_number_stored_as_a_number_counts(self) -> None:
        contact = _contact(custom_data={"dni": 12345678, "nombres": "Ana"})

        facts = _build(_snapshot(contact=contact))

        assert facts.datos_faltantes == []

    def test_no_contact_at_all_misses_every_field_even_the_phone(self) -> None:
        """The simulator talks without a contact."""
        facts = _build(_snapshot())

        assert facts.datos_faltantes == ["nombres", "telefono"]

    def test_the_templates_own_list_is_what_is_asked(self) -> None:
        services = TEMPLATES["servicios"].resolve()

        facts = _build(_snapshot(contact=_contact()), options=services)

        assert facts.datos_faltantes == ["nombres", "empresa", "correo"]


class TestNames:
    def test_a_name_the_customer_gave_is_confirmed(self) -> None:
        contact = _contact(profile_name="ana_92", custom_data={"nombres": " Ana "})

        facts = _build(_snapshot(contact=contact))

        assert (facts.nombre_cliente, facts.nombre_confirmado) == ("Ana", True)

    def test_the_profile_name_is_shown_but_not_confirmed(self) -> None:
        facts = _build(_snapshot(contact=_contact(profile_name="ana_92")))

        assert (facts.nombre_cliente, facts.nombre_confirmado) == ("ana_92", False)

    def test_no_name_at_all(self) -> None:
        assert _build(_snapshot(contact=_contact())).nombre_cliente is None
        assert _build(_snapshot()).nombre_cliente is None
        assert _build(_snapshot()).nombre_confirmado is False


class TestDelivery:
    """C-22: delivery comes from the business information, not from the template."""

    PHYSICAL = [(_item(), False)]

    def _facts(self, entregas: Entregas | None, **order: Any) -> Facts:
        contact = _contact(custom_data={"nombres": "Ana"})
        return _build(
            _snapshot(contact=contact, order=_order(**order), lines=self.PHYSICAL),
            entregas=entregas,
        )

    def test_no_delivery_section_means_no_delivery_data(self) -> None:
        facts = self._facts(None)

        assert facts.requiere_entrega is False
        assert facts.datos_faltantes == []

    def test_a_business_with_neither_delivery_nor_pickup_asks_nothing(self) -> None:
        facts = self._facts(_entregas(hace_delivery=False, recojo_en_tienda=False))

        assert facts.requiere_entrega is False

    def test_the_customer_chooses_when_both_are_offered(self) -> None:
        facts = self._facts(_entregas(recojo_en_tienda=True))

        assert facts.requiere_entrega is True
        assert facts.datos_faltantes == ["tipo_entrega"]

    def test_delivery_asks_for_the_address_and_the_zone(self) -> None:
        zonas = [Zona(nombre="Centro", costo=Decimal("8.00"), tiempo="mismo día")]

        facts = self._facts(_entregas(zonas=zonas), tipo_entrega="delivery")

        assert facts.datos_faltantes == ["direccion", "zona_envio"]
        assert facts.datos_completos is False

    def test_a_delivery_without_zones_needs_no_zone(self) -> None:
        facts = self._facts(_entregas(zonas=[]), tipo_entrega="delivery")

        assert facts.datos_faltantes == ["direccion"]

    def test_the_zone_and_address_complete_a_delivery(self) -> None:
        zonas = [Zona(nombre="Centro", costo=Decimal("8.00"), tiempo="mismo día")]
        contact = _contact(custom_data={"nombres": "Ana", "direccion": "Av. Lima 123"})
        snapshot = _snapshot(
            contact=contact,
            order=_order(tipo_entrega="delivery", zona_envio="Centro"),
            lines=self.PHYSICAL,
        )

        facts = _build(snapshot, entregas=_entregas(zonas=zonas))

        assert facts.datos_faltantes == []
        assert facts.datos_completos is True

    def test_pickup_needs_nothing_else(self) -> None:
        facts = self._facts(_entregas(recojo_en_tienda=True), tipo_entrega="recojo")

        assert facts.requiere_entrega is True
        assert facts.datos_faltantes == []

    def test_a_pickup_only_business_fixes_the_type_by_itself(self) -> None:
        facts = self._facts(_entregas(hace_delivery=False, recojo_en_tienda=True))

        assert facts.requiere_entrega is True
        assert facts.datos_faltantes == []

    def test_a_delivery_only_business_fixes_the_type_by_itself(self) -> None:
        """Milestone B, decision 11: the type is not asked; the address is."""
        facts = self._facts(_entregas(hace_delivery=True, recojo_en_tienda=False, zonas=[]))

        assert facts.requiere_entrega is True
        assert facts.datos_faltantes == ["direccion"]

    def test_the_address_is_not_asked_twice_when_the_template_already_requires_it(self) -> None:
        options = TEMPLATES["productos"].resolve(
            TEMPLATES["productos"].validate_options({"datos_requeridos": ["nombres", "direccion"]})
        )
        contact = _contact(custom_data={"nombres": "Ana"})
        snapshot = _snapshot(
            contact=contact, order=_order(tipo_entrega="delivery"), lines=self.PHYSICAL
        )

        facts = _build(snapshot, options=options, entregas=_entregas())

        assert facts.datos_faltantes == ["direccion"]

    def test_a_digital_product_needs_no_delivery(self) -> None:
        contact = _contact(custom_data={"nombres": "Ana"})
        snapshot = _snapshot(contact=contact, order=_order(), lines=[(_item(), True)])

        facts = _build(snapshot, entregas=_entregas(recojo_en_tienda=True))

        assert facts.requiere_entrega is False
        assert facts.datos_faltantes == []

    def test_one_physical_line_among_digital_ones_requires_delivery(self) -> None:
        contact = _contact(custom_data={"nombres": "Ana"})
        snapshot = _snapshot(
            contact=contact,
            order=_order(),
            lines=[(_item(nombre="Curso"), True), (_item(nombre="Libro"), False)],
        )

        facts = _build(snapshot, entregas=_entregas(recojo_en_tienda=True))

        assert facts.requiere_entrega is True
        assert facts.datos_faltantes == ["tipo_entrega"]

    def test_without_an_order_there_is_no_delivery_to_collect(self) -> None:
        contact = _contact(custom_data={"nombres": "Ana"})

        facts = _build(_snapshot(contact=contact), entregas=_entregas())

        assert facts.requiere_entrega is False
        assert facts.datos_faltantes == []


class TestThePaymentMethodsFlag:
    def _facts(self, registro_at: datetime | None, order: Order, **snapshot: Any) -> Facts:
        stored: dict[str, Any] = {}
        if registro_at is not None:
            stored["metodos_pago_enviados"] = {
                "order_id": str(order.id),
                "en": registro_at.isoformat(),
            }
        return _build(_snapshot(order=order, state=_state(stored), **snapshot))

    def test_sent_for_the_active_order_counts(self) -> None:
        facts = self._facts(NOW, _order())

        assert facts.metodos_pago_enviados is True

    def test_never_sent_does_not(self) -> None:
        assert self._facts(None, _order()).metodos_pago_enviados is False

    def test_sent_for_another_order_does_not(self) -> None:
        """A new order after CERRADO is sent the methods again."""
        stored = {"metodos_pago_enviados": {"order_id": str(uuid.uuid4()), "en": NOW.isoformat()}}

        facts = _build(_snapshot(order=_order(), state=_state(stored)))

        assert facts.metodos_pago_enviados is False

    def test_sent_before_the_last_rejection_does_not_count(self) -> None:
        rejected_at = NOW + timedelta(hours=1)

        facts = self._facts(NOW, _order(), last_rejection_at=rejected_at)

        assert facts.metodos_pago_enviados is False

    def test_sent_after_the_last_rejection_counts(self) -> None:
        rejected_at = NOW - timedelta(hours=1)

        facts = self._facts(NOW, _order(), last_rejection_at=rejected_at)

        assert facts.metodos_pago_enviados is True

    def test_no_active_order_means_not_sent(self) -> None:
        stored = {"metodos_pago_enviados": {"order_id": str(uuid.uuid4()), "en": NOW.isoformat()}}

        facts = _build(_snapshot(state=_state(stored)))

        assert facts.metodos_pago_enviados is False
        assert facts.metodos_pago_registro is not None  # the record itself is kept


class TestRejectionAndProof:
    def test_a_live_rejection_with_its_reason(self) -> None:
        order = _order(rechazo_vigente=True, motivo_rechazo="Monto distinto")
        snapshot = _snapshot(order=order, lines=[(_item(), False)], last_proof=_proof("rechazado"))

        facts = _build(snapshot)

        assert facts.pago_estado == "rechazado"
        assert facts.motivo_rechazo == "Monto distinto"

    def test_a_cash_on_delivery_rejection_keeps_its_reason_and_returns_to_a_draft(self) -> None:
        order = _order(modalidad_pago="contra_entrega", motivo_rechazo="Dirección incompleta")

        facts = _build(_snapshot(order=order, lines=[(_item(), False)]))

        assert facts.pago_estado is None
        assert facts.motivo_rechazo == "Dirección incompleta"

    def test_the_reason_is_not_carried_by_a_confirmed_order(self) -> None:
        order = _order(estado="confirmado", motivo_rechazo="Antiguo")

        assert _build(_snapshot(order=order)).motivo_rechazo is None

    def test_the_observations_of_the_latest_proof_are_exposed(self) -> None:
        proof = _proof(observaciones=["monto_distinto", "baja_confianza"])

        facts = _build(_snapshot(order=_order(), last_proof=proof))

        assert facts.comprobante is not None
        assert facts.comprobante.observaciones == ["monto_distinto", "baja_confianza"]

    def test_no_proof_no_comprobante(self) -> None:
        assert _build(_snapshot(order=_order())).comprobante is None


class TestClosingPromotions:
    def test_only_closing_promotions_are_listed(self) -> None:
        automatic = _promo(nombre="Automática", uso="automatica", vigencia_horas=None)
        closing = _promo(nombre="Cupón")

        facts = _build(_snapshot(), promotions=[automatic, closing])

        assert [p.nombre for p in facts.promos_cierre] == ["Cupón"]
        assert facts.promos_cierre[0].vigencia_horas == 24
        assert facts.promos_cierre[0].ofrecida_vence is None

    def test_an_offered_promotion_shows_when_it_expires(self) -> None:
        promo = _promo()
        expiry = NOW + timedelta(hours=5)
        stored = {"promos_ofrecidas": {str(promo.id): expiry.isoformat()}}

        facts = _build(_snapshot(state=_state(stored)), promotions=[promo])

        assert facts.promos_cierre[0].ofrecida_vence == expiry

    def test_an_expired_offer_is_as_good_as_never_made(self) -> None:
        promo = _promo()
        stored = {"promos_ofrecidas": {str(promo.id): (NOW - timedelta(minutes=1)).isoformat()}}

        facts = _build(_snapshot(state=_state(stored)), promotions=[promo])

        assert facts.promos_cierre[0].ofrecida_vence is None

    def test_an_offer_for_another_promotion_does_not_leak_across(self) -> None:
        promo = _promo()
        stored = {"promos_ofrecidas": {str(uuid.uuid4()): (NOW + timedelta(hours=1)).isoformat()}}

        facts = _build(_snapshot(state=_state(stored)), promotions=[promo])

        assert facts.promos_cierre[0].ofrecida_vence is None

    def test_no_promotions(self) -> None:
        assert _build(_snapshot()).promos_cierre == []

    def test_with_an_order_only_the_promotions_that_apply_to_it_are_listed(self) -> None:
        """TASK-424: a coupon the order cannot use is not shown to the model."""
        course = uuid.uuid4()
        other = uuid.uuid4()
        fits = _promo(
            nombre="Cupón cursos",
            tipo="porcentaje",
            valor=Decimal("20"),
            aplica_a="productos",
            product_ids=[course],
        )
        wrong_product = _promo(
            nombre="Cupón otro",
            tipo="porcentaje",
            valor=Decimal("20"),
            aplica_a="productos",
            product_ids=[other],
        )
        too_small = _promo(
            nombre="Cupón grande", tipo="porcentaje", valor=Decimal("10"), monto_minimo=Decimal(900)
        )
        item = _item(product_id=course, precio_unitario=Decimal("500.00"))

        facts = _build(
            _snapshot(order=_order(), lines=[(item, False)]),
            promotions=[fits, wrong_product, too_small],
        )

        assert [p.nombre for p in facts.promos_cierre] == ["Cupón cursos"]

    def test_a_category_promotion_reads_the_category_of_the_line(self) -> None:
        promo = _promo(
            tipo="porcentaje", valor=Decimal("15"), aplica_a="categorias", categorias=["Gestión"]
        )
        item = _item(product_id=uuid.uuid4(), precio_unitario=Decimal("100.00"))
        snapshot = ConversationSnapshot(
            order=_order(), lines=[OrderLine(item=item, es_digital=True, categoria="gestión")]
        )

        assert len(_build(snapshot, promotions=[promo]).promos_cierre) == 1

    def test_without_lines_every_closing_promotion_in_force_is_listed(self) -> None:
        promo = _promo(tipo="porcentaje", valor=Decimal("20"), aplica_a="productos")

        facts = _build(_snapshot(order=_order()), promotions=[promo])

        assert [p.nombre for p in facts.promos_cierre] == ["Cupón 20 %"]


class TestStoredFactsPassThrough:
    def test_the_stored_facts_are_read_from_the_state_row(self) -> None:
        stored = {
            "saludo_enviado": True,
            "camino": "programa",
            "vio_productos": True,
            "recursos_enviados": ["saludo"],
            "derivado": {
                "motivo": "asesoria",
                "resumen": "x",
                "hasta": (NOW + timedelta(hours=2)).isoformat(),
            },
        }

        facts = _build(_snapshot(state=_state(stored)))

        assert facts.saludo_enviado is True
        assert facts.camino == "programa"
        assert facts.recursos_enviados == ["saludo"]
        assert facts.derivado_activo(NOW) is True

    def test_a_missing_state_row_is_a_conversation_nobody_has_answered(self) -> None:
        facts = _build(_snapshot())

        assert facts.saludo_enviado is False
        assert facts.derivado is None
