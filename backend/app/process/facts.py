"""What the engine knows about a conversation (Feature 004, `data-model.md` 0014).

`Facts` is the value the stage engine reads. It has two kinds of field (C-03):

* **Stored** facts live in `conversation_state.facts` because they only exist at
  conversation level: whether the greeting went out, the ad the customer came
  from, the chosen path, which resources were already sent...
* **Derived** facts are computed on every turn from their source table (the
  order, the customer's data, the payment proofs) and never written back, so
  they cannot drift from it.

The model comes first. `FactsLoader` at the end reads the rows (three statements,
see `ConversationSnapshotRepository`) and `build_facts` turns them into a
`Facts`. `build_facts` is pure, so every rule of the derived facts is a
table-driven test.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order, PaymentProof
from app.models.sales_config import Promotion
from app.orders.pricing import Line as PricingLine
from app.orders.pricing import Rule, applies_to_order
from app.process.templates.base import EffectiveOptions
from app.repositories.conversation_snapshot import (
    ConversationSnapshot,
    ConversationSnapshotRepository,
)
from app.schemas.business_info import Entregas

PaymentState = Literal["por_verificar", "rechazado", "confirmado"]

# Order states that mean the sale is made (the operator confirmed it).
CONFIRMED_ORDER_STATES = frozenset({"confirmado", "en_curso", "completado"})

# The keys `conversation_state.facts` may hold (C-03). Anything derived is
# computed each turn and never written there, so `from_stored` drops it.
STORED_KEYS = frozenset(
    {
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
    }
)


class _Model(BaseModel):
    # Facts are read from JSON written by earlier versions of the code: a key
    # this version no longer knows must not make the whole conversation unreadable.
    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------- stored


class EntradaAnuncio(_Model):
    """The Meta ad a customer arrived from (`referral` in the webhook)."""

    ad_id: str | None = None
    source_url: str | None = None
    headline: str | None = None
    body: str | None = None
    recibido_en: datetime


class MetodosPagoEnviados(_Model):
    """When the payment methods were sent, for which order."""

    order_id: uuid.UUID
    en: datetime


class Derivado(_Model):
    """A conversation handed to a person, and until when the bot stays quiet."""

    motivo: str
    resumen: str | None = None
    hasta: datetime
    # When it began, and whether the business was told nobody took it (Feature 007).
    desde: datetime | None = None
    avisado: bool = False
    # A quote's structured data (feature 019, phase B): what the customer already said.
    datos: dict[str, str] = Field(default_factory=dict)


class LeadCalificado(_Model):
    """What the assistant learned about a customer and how interested they are (019, phase E)."""

    interes: Literal["alto", "medio", "bajo"]
    motivo: str | None = None
    respuestas: dict[str, str] = Field(default_factory=dict)
    en: datetime | None = None


class Seguimiento(_Model):
    """Follow-up messages already sent in this stage of silence."""

    intentos: int = 0
    ultimo_en: datetime | None = None


# ---------------------------------------------------------------- derived


class PedidoItem(_Model):
    nombre: str
    cantidad: int = 1
    selecciones: list[str] = Field(default_factory=list)


class Pedido(_Model):
    """The active order, as the engine and the prompt see it."""

    id: uuid.UUID | None = None
    numero: int | None = None
    estado: str = "borrador"
    items: list[PedidoItem] = Field(default_factory=list)
    # Every package has `bundle_size x cantidad` selections.
    completo: bool = True
    faltan_selecciones: int = 0
    total: Decimal = Decimal(0)
    moneda: str = "PEN"


class Comprobante(_Model):
    """What the vision step flagged on the latest payment proof."""

    observaciones: list[str] = Field(default_factory=list)


class PromoCierre(_Model):
    """A closing promotion the assistant may offer, and whether it already did."""

    id: str
    nombre: str
    vigencia_horas: int
    ofrecida_vence: datetime | None = None


class Facts(_Model):
    """Everything the engine, the prompt and the tools read about a conversation."""

    # ---- stored (conversation_state.facts)
    saludo_enviado: bool = False
    entrada_anuncio: EntradaAnuncio | None = None
    camino: str | None = None
    # The package the customer named before there was an order to put it in
    # ("quiero el paquete de 5"), read from their words (`bundle_request`).
    paquete_pedido: str | None = None
    # Set when a product search returned results or a product list was sent.
    vio_productos: bool = False
    recursos_enviados: list[str] = Field(default_factory=list)
    opciones_mostradas: list[str] = Field(default_factory=list)
    # Stored under the key `metodos_pago_enviados`; the derived flag of that
    # name below says whether it still counts for the active order.
    metodos_pago_registro: MetodosPagoEnviados | None = None
    derivado: Derivado | None = None
    # Kept after the pause ends, so advisory handovers are not repeated.
    asesoria_derivada_en: datetime | None = None
    # When the customer booked an appointment (feature 019, phase D): a qualified lead.
    cita_agendada: datetime | None = None
    # How warm the customer is, as the assistant judged it (`captar`).
    lead_calificado: LeadCalificado | None = None
    seguimiento: Seguimiento | None = None
    # Closing promotions offered in this conversation, with their expiry.
    promos_ofrecidas: dict[str, datetime] = Field(default_factory=dict)

    # ---- derived (computed every turn)
    nombre_cliente: str | None = None
    nombre_confirmado: bool = False
    pedido: Pedido | None = None
    datos_completos: bool = False
    datos_faltantes: list[str] = Field(default_factory=list)
    requiere_entrega: bool = False
    pago_estado: PaymentState | None = None
    modalidad_pago: Literal["previo", "contra_entrega"] = "previo"
    # True if the methods were sent for the active order and after the last rejection.
    metodos_pago_enviados: bool = False
    motivo_rechazo: str | None = None
    comprobante: Comprobante | None = None
    # A receipt arrived but the order was not complete: it waits on the draft and
    # goes to the operator once the order is complete (finding E-1).
    comprobante_en_espera: bool = False
    promos_cierre: list[PromoCierre] = Field(default_factory=list)

    def derivado_activo(self, now: datetime) -> bool:
        """Whether the bot is paused for a person right now."""
        return self.derivado is not None and self.derivado.hasta > now

    @classmethod
    def from_stored(cls, stored: Mapping[str, Any], **derived: Any) -> Facts:
        """Facts from the stored JSON plus the values derived this turn.

        Only `STORED_KEYS` are read from `stored`: a derived fact that ended up
        there by mistake must not override the one computed from its source.
        """
        values = {key: value for key, value in stored.items() if key in STORED_KEYS}
        if "metodos_pago_enviados" in values:
            values["metodos_pago_registro"] = values.pop("metodos_pago_enviados")
        return cls.model_validate({**values, **derived})

    def to_stored(self) -> dict[str, Any]:
        """The stored facts as the JSON `conversation_state.facts` holds.

        Derived facts are left out on purpose (C-03), and the payment-methods
        record goes back under its stored key.
        """
        stored = self.model_dump(
            mode="json",
            include={
                "saludo_enviado",
                "entrada_anuncio",
                "camino",
                "paquete_pedido",
                "vio_productos",
                "recursos_enviados",
                "opciones_mostradas",
                "derivado",
                "asesoria_derivada_en",
                "cita_agendada",
                "lead_calificado",
                "seguimiento",
                "promos_ofrecidas",
            },
            exclude_none=True,
        )
        if self.metodos_pago_registro is not None:
            stored["metodos_pago_enviados"] = self.metodos_pago_registro.model_dump(mode="json")
        return stored


# ------------------------------------------------------------------- loader


@dataclass(frozen=True)
class FactsConfig:
    """The configuration the derived facts depend on.

    Passed in rather than read here: it changes rarely and the caller
    (`ProcessConfigService`, TASK-414) caches it, so the loader stays at three
    statements per turn.
    """

    options: EffectiveOptions
    # `business_info` section "entregas"; None if the business never filled it.
    entregas: Entregas | None = None
    # The promotions currently in force (`PromotionRepository.in_force`).
    promotions: Sequence[Promotion] = ()


def payment_state(order: Order | None, last_proof: PaymentProof | None) -> PaymentState | None:
    """`pago_estado` (`data-model.md`, 0019).

    | order                                     | payment state |
    | confirmado / en_curso / completado        | confirmado    |
    | por_confirmar                             | por_verificar |
    | borrador, rechazo_vigente, proof rejected | rechazado     |
    | anything else                             | None          |
    """
    if order is None:
        return None
    if order.estado in CONFIRMED_ORDER_STATES:
        return "confirmado"
    if order.estado == "por_confirmar":
        return "por_verificar"
    if (
        order.estado == "borrador"
        and order.rechazo_vigente
        and last_proof is not None
        and last_proof.estado == "rechazado"
    ):
        return "rechazado"
    return None


def _customer_has(field: str, contact: Contact | None) -> bool:
    """Whether the customer's data already holds `field`."""
    if contact is None:
        return False
    if field == "telefono" and contact.phone_e164:
        # The phone they write from is their phone: no need to ask again.
        return True
    value = contact.custom_data.get(field)
    return value is not None and str(value).strip() != ""


def _requires_delivery(snapshot: ConversationSnapshot, entregas: Entregas | None) -> bool:
    """An order needs delivery data when something in it is physical and the
    business offers delivery or pickup (`plantillas.md` section 1, C-22)."""
    if entregas is None or not (entregas.hace_delivery or entregas.recojo_en_tienda):
        return False
    return any(not line.es_digital for line in snapshot.lines)


def only_delivery_type(entregas: Entregas | None) -> Literal["recojo", "delivery"] | None:
    """The delivery type when the business offers just one, else None.

    A shop that only delivers, or only lets people pick up, never asks the
    customer to choose (`plantillas.md` section 1; delivery added by Giannier,
    Milestone B, decision 11).
    """
    if entregas is None:
        return None
    if entregas.recojo_en_tienda and not entregas.hace_delivery:
        return "recojo"
    if entregas.hace_delivery and not entregas.recojo_en_tienda:
        return "delivery"
    return None


def missing_customer_data(
    contact: Contact | None,
    datos_requeridos: Sequence[str],
    entregas: Entregas | None,
    *,
    requiere_entrega: bool,
    tipo_entrega: str | None,
    zona_envio: str | None,
) -> list[str]:
    """What is still missing from the customer's data, in the order it is asked.

    The fields the template requires that the customer has not given, then - only
    when the order needs delivery (C-22) - the delivery choice: the type, the
    address for a delivery, the zone when the business has zones. A business that
    only offers pickup, or only delivers, does not make the customer choose. Shared by the facts
    (`datos_faltantes`) and by `registrar_datos_cliente`, which answers `faltan`
    with fresh data, so the two can never disagree.
    """
    missing = [f for f in datos_requeridos if not _customer_has(f, contact)]
    if not requiere_entrega or entregas is None:
        return missing

    tipo = tipo_entrega or only_delivery_type(entregas)
    if tipo is None:
        missing.append("tipo_entrega")
    elif tipo == "delivery":
        if "direccion" not in missing and not _customer_has("direccion", contact):
            missing.append("direccion")
        # No zones configured means the cost is "a coordinar": no zone to pick.
        if entregas.zonas and not zona_envio:
            missing.append("zona_envio")
    return missing


def _missing_data(
    snapshot: ConversationSnapshot, config: FactsConfig, *, requiere_entrega: bool
) -> list[str]:
    order = snapshot.order
    return missing_customer_data(
        snapshot.contact,
        config.options.datos_requeridos,
        config.entregas,
        # Without an order there is nothing to deliver.
        requiere_entrega=requiere_entrega and order is not None,
        tipo_entrega=order.tipo_entrega if order is not None else None,
        zona_envio=order.zona_envio if order is not None else None,
    )


def _pedido(snapshot: ConversationSnapshot) -> Pedido | None:
    order = snapshot.order
    if order is None:
        return None
    items: list[PedidoItem] = []
    missing_selections = 0
    for line in snapshot.lines:
        item = line.item
        chosen = [str(selection.get("nombre", "")) for selection in item.bundle_selections]
        items.append(PedidoItem(nombre=item.nombre, cantidad=item.cantidad, selecciones=chosen))
        if item.bundle_size:
            missing_selections += max(0, item.bundle_size * item.cantidad - len(chosen))
    return Pedido(
        id=order.id,
        numero=order.numero,
        estado=order.estado,
        items=items,
        completo=bool(items) and missing_selections == 0,
        faltan_selecciones=missing_selections,
        total=order.total,
        moneda=order.moneda,
    )


def _name(contact: Contact | None) -> tuple[str | None, bool]:
    """The customer's name and whether they told us (versus WhatsApp's label)."""
    if contact is None:
        return None, False
    told = contact.custom_data.get("nombres")
    if told is not None and str(told).strip():
        return str(told).strip(), True
    return contact.profile_name or None, False


def _pricing_lines(snapshot: ConversationSnapshot) -> list[PricingLine]:
    return [
        PricingLine(
            product_id=line.item.product_id,
            categoria=line.categoria,
            precio_unitario=line.item.precio_unitario,
            cantidad=line.item.cantidad,
        )
        for line in snapshot.lines
    ]


def _closing_promotions(
    promotions: Sequence[Promotion],
    offered: Mapping[str, Any],
    now: datetime,
    lines: Sequence[PricingLine] = (),
) -> list[PromoCierre]:
    """The closing promotions the assistant may offer, each with its expiry if
    already offered.

    With an order that has lines, only the ones that would take something off it
    (scope, minimum amount, quantity: `pricing.applies_to_order`) are listed, so
    the model is never shown a coupon it cannot honour. With no lines there is
    nothing to judge against and every one in force is listed: "I'll think about
    it" before an order exists is exactly when a coupon is offered.
    """
    closing: list[PromoCierre] = []
    for promotion in promotions:
        if promotion.uso != "cierre":
            continue
        if lines and not applies_to_order(Rule.from_promotion(promotion), lines, now):
            continue
        expiry = offered.get(str(promotion.id))
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry)
        closing.append(
            PromoCierre(
                id=str(promotion.id),
                nombre=promotion.nombre,
                vigencia_horas=promotion.vigencia_horas or 0,
                # An offer that already expired is as good as never made.
                ofrecida_vence=expiry if expiry is not None and expiry > now else None,
            )
        )
    return closing


def build_facts(snapshot: ConversationSnapshot, config: FactsConfig, now: datetime) -> Facts:
    """Stored facts plus the facts derived from the rows of the snapshot."""
    stored = snapshot.state.facts if snapshot.state is not None else {}
    order = snapshot.order
    registro_raw = stored.get("metodos_pago_enviados")
    registro = MetodosPagoEnviados.model_validate(registro_raw) if registro_raw else None

    requiere_entrega = _requires_delivery(snapshot, config.entregas)
    missing = _missing_data(snapshot, config, requiere_entrega=requiere_entrega)
    nombre, confirmado = _name(snapshot.contact)

    # The methods count only for the active order and only if they were sent
    # after the business last rejected a proof (C-21): a rejected customer is
    # sent the methods again, once.
    methods_sent = (
        registro is not None
        and order is not None
        and registro.order_id == order.id
        and (snapshot.last_rejection_at is None or registro.en > snapshot.last_rejection_at)
    )

    return Facts.from_stored(
        stored,
        nombre_cliente=nombre,
        nombre_confirmado=confirmado,
        pedido=_pedido(snapshot),
        datos_completos=not missing,
        datos_faltantes=missing,
        requiere_entrega=requiere_entrega,
        pago_estado=payment_state(order, snapshot.last_proof),
        modalidad_pago=(
            order.modalidad_pago if order is not None else config.options.modalidad_pago
        ),
        metodos_pago_enviados=methods_sent,
        motivo_rechazo=(
            order.motivo_rechazo if order is not None and order.estado == "borrador" else None
        ),
        comprobante=(
            Comprobante(observaciones=list(snapshot.last_proof.observaciones))
            if snapshot.last_proof is not None
            else None
        ),
        comprobante_en_espera=(
            order is not None
            and order.estado == "borrador"
            and snapshot.last_proof is not None
            and snapshot.last_proof.estado == "por_verificar"
        ),
        promos_cierre=_closing_promotions(
            config.promotions, stored.get("promos_ofrecidas", {}), now, _pricing_lines(snapshot)
        ),
    )


class FactsLoader:
    """Loads the facts of a conversation: stored plus derived, in three reads."""

    def __init__(self, session: Session, tenant_id: uuid.UUID, config: FactsConfig) -> None:
        self._snapshots = ConversationSnapshotRepository(session, tenant_id)
        self._config = config

    def load(self, conversation: Conversation, *, now: datetime | None = None) -> Facts:
        """The facts of `conversation` as of `now` (default: the current time)."""
        snapshot = self._snapshots.load(conversation)
        return build_facts(snapshot, self._config, now or datetime.now(UTC))
