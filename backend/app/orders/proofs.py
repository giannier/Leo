"""The proof pipeline: a photo in, a payment proof recorded (Feature 004, TASK-429, C-08).

In Capacita v21 the model read the receipt and called a "register payment" tool; if
it forgot, the payment was lost. Here the system does it, *before* the model is
called (step 4 of the turn pipeline), so a receipt cannot be forgotten, and it also
runs while a conversation is handed to a person:

1. Vision reads the image (`vision.py`, 8 s at most, never raises).
2. If it is a receipt - or could not be read at all, which is not the same as
   "not a receipt" - the image goes to private storage and a `payment_proof` is
   created `por_verificar` on the order.
3. What looks off is flagged, never rejected: a different or negative amount, an
   operation code seen before, low confidence, nothing readable, a recipient that
   is not the business's (or that nobody could check), an operation dated before
   the order. The operator decides (RF-023). An empty list of observations means
   every check ran and passed, nothing less.
4. If the order is complete - lines, package selections, the customer's data -
   it moves to `por_confirmar`, the operator is told and the model says it is in
   review. If not, nobody is told yet: the proof waits on the order, the model asks
   for what is missing, and `submit_waiting` sends it once the order is complete
   (Milestone B, finding E-1). A second photo while a proof waits is not another
   proof and not another notice.

An image that vision says is *not* a receipt is not stored at all: it is a stranger's
photo, not a payment record.

Cash-on-delivery orders have no proof (C-07): an image there is just an image.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.agent.tools.shared import CURRENCY_SYMBOLS, money
from app.core.config import get_settings
from app.models.business import BusinessProfile
from app.models.channel import Contact
from app.models.conversation import Conversation
from app.models.orders import Order, PaymentProof
from app.orders.service import OrderError, OrderService
from app.orders.vision import ProofReading, read_proof, unreadable
from app.process.facts import missing_customer_data
from app.process.service import ProcessConfigService
from app.providers.llm.base import LLMProvider
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.whatsapp.base import InboundMedia
from app.repositories.channel import ContactRepository
from app.repositories.orders import PaymentProofRepository
from app.repositories.payment import PaymentMethodRepository

logger = logging.getLogger(__name__)

# Below this the reading is flagged `baja_confianza`. A number nobody set in the
# docs: a conservative start, one constant to move once real receipts show where
# the model is wrong.
LOW_CONFIDENCE = Decimal("0.70")

OBS_AMOUNT = "monto_distinto"
OBS_NEGATIVE = "monto_negativo"
OBS_CODE = "codigo_repetido"
OBS_CONFIDENCE = "baja_confianza"
OBS_NO_DATA = "sin_datos"
OBS_RECIPIENT = "destinatario_distinto"
OBS_RECIPIENT_UNCHECKED = "destinatario_no_verificado"
OBS_EARLY_DATE = "fecha_anterior_al_pedido"

DEFAULT_TIMEZONE = "America/Lima"

# The event an unrecognised image leaves on the order (finding D-4).
UNRECOGNISED_NOTE = (
    "El cliente envió una imagen que no parece un comprobante. Revísala por si es un pago."
)

# What the model is told when the receipt is kept but the order is not complete.
_MISSING_LABELS = {
    "selecciones": "completar los temas del paquete",
    "pedido": "agregar al menos un producto al pedido",
    "contacto": "identificar al cliente",
}

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

Kind = Literal["registrado", "no_comprobante", "ignorado"]


class ProofNotices(Protocol):
    """Tells the business a receipt arrived (TASK-431 implements it)."""

    def new_proof(self, order: Order, proof: PaymentProof) -> None: ...


@dataclass(frozen=True)
class ReceivedImage:
    """A photo the customer sent, in memory."""

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class InboundImage:
    """An image message of the burst, as the pipeline knows it before fetching it.

    Either already in private storage (`stored` = key and type: the simulator) or
    still with the provider (`media`: the real channel).
    """

    message_id: uuid.UUID
    media: InboundMedia | None = None
    stored: tuple[str, str] | None = None


class ImageReader(Protocol):
    """Fetches the bytes of an inbound image; None if they cannot be had."""

    async def read(self, image: InboundImage) -> ReceivedImage | None: ...


@dataclass(frozen=True)
class ProofOutcome:
    """What the pipeline did with one image."""

    kind: Kind
    # The line for "ESTE TURNO"; None when the image was ignored.
    context: str | None = None
    proof: PaymentProof | None = None
    order: Order | None = None


class ProofPipeline:
    """Turns a receipt photo into a payment proof on the active order."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        llm: LLMProvider,
        storage: StorageProvider | None,
        notices: ProofNotices | None = None,
        payees: Sequence[str] | None = None,
        process_config: ProcessConfigService | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._llm = llm
        self._storage = storage
        self._notices = notices
        # The names the business collects under: the `titular` of its active
        # payment methods (017 RF-010, 004 D-1) unless the caller passes them.
        # None configured: no recipient can be checked, and every proof says so.
        if payees is None:
            payees = [
                method.titular
                for method in PaymentMethodRepository(session, tenant_id).list_active()
                if method.titular
            ]
        self._payees = tuple(name for name in payees if _name_tokens(name))
        self._process = process_config or ProcessConfigService(session)
        self._orders = OrderService(session, tenant_id)
        self._proofs = PaymentProofRepository(session, tenant_id)

    async def handle(
        self,
        conversation: Conversation,
        image: ReceivedImage,
        *,
        message_id: uuid.UUID | None = None,
        read: bool = True,
    ) -> ProofOutcome:
        """Record one image as a payment proof if it is one.

        `read=False` (the conversation is paused, handed to a person or the
        customer opted out: hallazgo P-1) stores the image for the operator
        without sending it to any model. So does a platform whose vision is not
        approved (`VISION_ENABLED=false`).
        """
        order = self._orders.active_for(conversation.id)
        if order is None or order.estado != "borrador" or order.modalidad_pago != "previo":
            # No order to pay, one already waiting, or cash on delivery: a receipt
            # has nothing to attach to.
            return ProofOutcome("ignorado")
        if self._storage is None:
            logger.warning("a receipt cannot be stored: no object storage is configured")
            return ProofOutcome("ignorado")

        contact = self._contact(conversation)
        waiting = self.waiting_proof(order)
        if waiting is not None:
            # The receipt is already on the order; it waits for the order to be
            # complete. Another photo is neither another proof nor another notice.
            return ProofOutcome(
                "ignorado",
                _waiting_line(self.missing(order, contact)),
                proof=waiting,
                order=order,
            )

        if not read:
            reading = unreadable("revision_humana")
        elif not get_settings().vision_enabled:
            reading = unreadable("vision_no_aprobada")
        else:
            reading = await read_proof(self._llm, image.data, image.mime_type)
        if reading.legible and not reading.es_comprobante:
            self._keep_unrecognised(order, image)
            return ProofOutcome(
                "no_comprobante",
                "la imagen NO es un comprobante de pago; pídele la foto del comprobante",
                order=order,
            )

        try:
            key = self._store(image)
        except StorageError:
            logger.exception("a receipt could not be stored")
            return ProofOutcome(
                "ignorado",
                "no se pudo guardar la imagen; pídele que la envíe de nuevo",
                order=order,
            )

        proof = self._proofs.add(
            order_id=order.id,
            conversation_id=conversation.id,
            message_id=message_id,
            storage_key=key,
            estado="por_verificar",
            monto=reading.monto,
            moneda=reading.moneda,
            fecha_operacion=reading.fecha,
            codigo_operacion=reading.codigo,
            entidad=reading.entidad,
            destinatario=reading.destinatario,
            confianza=reading.confianza,
            observaciones=[],
            extraccion=_extraction(reading),
        )
        proof.observaciones = self.observations(order, proof, reading)
        self._session.flush()

        missing = self.missing(order, contact)
        if missing or contact is None or not self._submit(order, contact, proof):
            return ProofOutcome(
                "registrado",
                _incomplete_line(order, proof, reading, missing or ["pedido"]),
                proof,
                order,
            )
        return ProofOutcome("registrado", _describe(order, proof, reading), proof, order)

    def _keep_unrecognised(self, order: Order, image: ReceivedImage) -> None:
        """An image the model did not take for a receipt is kept, with an event on
        the order, so a real receipt the model misread is not lost (finding D-4).
        It is not a proof: no row in `payment_proofs`, no observation, no notice."""
        try:
            key = self._store(image)
        except StorageError:
            logger.exception("an unrecognised image could not be stored")
            return
        self._orders.add_note(
            order,
            UNRECOGNISED_NOTE,
            origen="sistema",
            evento="imagen_no_reconocida",
            imagen_id=str(uuid.uuid4()),
            imagen_key=key,
        )

    def submit_waiting(self, conversation: Conversation) -> Order | None:
        """Send the order to the operator if a proof waits on it and it is complete now.

        Called after the model's turn: the customer may have just given the data
        that were missing. Returns the order when it moved to `por_confirmar`.
        """
        order = self._orders.active_for(conversation.id)
        if order is None or order.estado != "borrador" or order.modalidad_pago != "previo":
            return None
        proof = self.waiting_proof(order)
        if proof is None:
            return None
        contact = self._contact(conversation)
        if contact is None or self.missing(order, contact):
            return None
        if not self._submit(order, contact, proof):
            return None
        self._session.flush()
        return order

    def waiting_proof(self, order: Order) -> PaymentProof | None:
        """The proof that waits on a draft order for it to be complete, if any."""
        latest = self._proofs.latest_for_order(order.id)
        if latest is None or latest.estado != "por_verificar":
            return None
        return latest

    def missing(self, order: Order, contact: Contact | None) -> list[str]:
        """What keeps the order from going to the operator, in the words the
        facts use (`datos_faltantes`) plus `pedido`, `selecciones` and `contacto`."""
        if contact is None:
            return ["contacto"]
        if not self._orders.lines(order):
            return ["pedido"]
        missing: list[str] = []
        if self._orders.missing_selections(order):
            missing.append("selecciones")
        options = self._process.effective(self._tenant_id).options
        missing.extend(
            missing_customer_data(
                contact,
                options.datos_requeridos,
                self._orders.entregas(),
                requiere_entrega=self._orders.requires_delivery(order),
                tipo_entrega=order.tipo_entrega,
                zona_envio=order.zona_envio,
            )
        )
        return missing

    # ---------------------------------------------------------------- helpers

    def observations(self, order: Order, proof: PaymentProof, reading: ProofReading) -> list[str]:
        """What looks off about a proof. Flags for the operator, never a verdict.

        An empty list must mean "every check ran and passed": a check that could
        not run (no accounts to compare the recipient with) is a flag too.
        """
        flags: list[str] = []
        if not reading.has_data:
            flags.append(OBS_NO_DATA)
        if reading.monto is not None and reading.monto < 0:
            # A reversal or a refund: never a payment, whatever its size.
            flags.append(OBS_NEGATIVE)
        if reading.monto is not None and (
            reading.monto != order.total
            or (reading.moneda is not None and reading.moneda != order.moneda)
        ):
            flags.append(OBS_AMOUNT)
        if reading.codigo is not None and self._proofs.code_seen(
            reading.codigo, sandbox=order.is_sandbox, exclude_id=proof.id
        ):
            flags.append(OBS_CODE)
        if reading.confianza is not None and reading.confianza < LOW_CONFIDENCE:
            flags.append(OBS_CONFIDENCE)
        recipient = self._recipient_flag(reading.destinatario)
        if recipient is not None:
            flags.append(recipient)
        if reading.fecha is not None and reading.fecha < self._order_day(order):
            flags.append(OBS_EARLY_DATE)
        return flags

    def _recipient_flag(self, destinatario: str | None) -> str | None:
        """`destinatario_distinto`, `destinatario_no_verificado` or None (it matched)."""
        if not self._payees or destinatario is None or not _name_tokens(destinatario):
            return OBS_RECIPIENT_UNCHECKED
        if any(_same_person(destinatario, payee) for payee in self._payees):
            return None
        return OBS_RECIPIENT

    def _order_day(self, order: Order) -> date:
        """The day the order was created, in the business's time zone.

        A receipt shows its local date; comparing it with a UTC day would flag a
        payment made at 8 pm in Lima on the day the order was created.
        """
        profile = self._session.get(BusinessProfile, self._tenant_id)
        try:
            zone = ZoneInfo(profile.timezone if profile is not None else DEFAULT_TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo(DEFAULT_TIMEZONE)
        return order.created_at.astimezone(zone).date()

    def _contact(self, conversation: Conversation) -> Contact | None:
        if conversation.contact_id is None:
            logger.warning("a receipt arrived on a conversation without a contact")
            return None
        return ContactRepository(self._session, self._tenant_id).get(conversation.contact_id)

    def _submit(self, order: Order, contact: Contact, proof: PaymentProof) -> bool:
        """The order waits for the operator, and the operator is told. False if the
        order rules refuse it: the proof stays on the draft and nobody is told."""
        try:
            self._orders.submit(order, contact)
        except OrderError:
            logger.warning(
                "a receipt arrived but its order cannot be submitted",
                extra={"tenant_id": str(self._tenant_id), "state": order.estado},
            )
            return False
        if self._notices is not None and not order.is_sandbox:
            self._notices.new_proof(order, proof)
        return True

    def _store(self, image: ReceivedImage) -> str:
        extension = _EXTENSIONS.get(image.mime_type, "bin")
        key = f"tenants/{self._tenant_id}/proofs/{uuid.uuid4()}.{extension}"
        assert self._storage is not None  # checked by the caller
        return self._storage.put(key, image.data, content_type=image.mime_type)


def _extraction(reading: ProofReading) -> dict[str, object]:
    """The raw reading for `payment_proofs.extraccion`: no image, nothing derived."""
    if not reading.legible:
        return {"legible": False, "motivo": reading.reason}
    return {"legible": True, "modelo": reading.model, "lectura": reading.raw}


def _describe(order: Order, proof: PaymentProof, reading: ProofReading) -> str:
    """The line the model reads: what came in, never a confirmation."""
    if not reading.legible:
        return (
            "comprobante recibido pero no se pudo leer; queda en revisión del negocio "
            "(no digas que está confirmado)"
        )
    symbol = CURRENCY_SYMBOLS.get(proof.moneda or order.moneda, proof.moneda or order.moneda)
    parts = [money(proof.monto, symbol)] if proof.monto is not None else []
    if proof.entidad:
        parts.append(proof.entidad)
    if proof.codigo_operacion:
        parts.append(f"op. {proof.codigo_operacion}")
    detail = ", ".join(parts) or "sin datos legibles"
    return (
        f"comprobante recibido: {detail}; queda en revisión del negocio "
        "(no digas que está confirmado)"
    )


def _incomplete_line(
    order: Order, proof: PaymentProof, reading: ProofReading, missing: Sequence[str]
) -> str:
    """The model's line when the receipt is kept but the order cannot go yet."""
    received = _describe(order, proof, reading).split(";", 1)[0]
    return (
        f"{received}; quedó guardado en el pedido, pero antes de pasarlo a revisión "
        f"falta: {_missing_text(missing)}. Pídeselo al cliente; no digas que está en "
        "revisión ni que está confirmado"
    )


def _waiting_line(missing: Sequence[str]) -> str:
    """The model's line for a photo that arrives while a proof already waits."""
    if not missing:
        return "ya tenemos su comprobante en el pedido; no hace falta que lo envíe de nuevo"
    return (
        "ya tenemos su comprobante en el pedido (no hace falta otro); para pasarlo a "
        f"revisión falta: {_missing_text(missing)}. Pídeselo al cliente"
    )


def _missing_text(missing: Sequence[str]) -> str:
    return ", ".join(_MISSING_LABELS.get(item, item) for item in missing)


_WORD_BREAK = re.compile(r"[\s.,;]+")
_NOT_A_LETTER = re.compile(r"[^a-z0-9]+")


def _name_tokens(name: str) -> set[str]:
    """The words of a name that can be compared: no accents, no case, no masked or
    short parts (`Juan P. Pér*` -> {"juan"})."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = (_NOT_A_LETTER.sub("", word) for word in _WORD_BREAK.split(plain) if "*" not in word)
    return {word for word in words if len(word) >= 3}


def _same_person(read: str, payee: str) -> bool:
    """Whether the recipient read on the receipt is the business's account holder.

    Wallets show the name shortened or masked ("Juan P. Pér*"), so every complete
    word read must be part of the holder's name; at least one word must be read.
    """
    read_tokens = _name_tokens(read)
    return bool(read_tokens) and read_tokens <= _name_tokens(payee)
