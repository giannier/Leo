"""What the engine knows about a conversation (Feature 004, `data-model.md` 0014).

`Facts` is the value the stage engine reads. It has two kinds of field (C-03):

* **Stored** facts live in `conversation_state.facts` because they only exist at
  conversation level: whether the greeting went out, the ad the customer came
  from, the chosen path, which resources were already sent...
* **Derived** facts are computed on every turn from their source table (the
  order, the customer's data, the payment proofs) and never written back, so
  they cannot drift from it.

This module is the model. `FactsLoader` (TASK-411) fills it in.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PaymentState = Literal["por_verificar", "rechazado", "confirmado"]


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
    promos_cierre: list[PromoCierre] = Field(default_factory=list)

    def derivado_activo(self, now: datetime) -> bool:
        """Whether the bot is paused for a person right now."""
        return self.derivado is not None and self.derivado.hasta > now
