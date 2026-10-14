"""Telling the business that something needs a person (Feature 004, TASK-431).

`contracts/api.md` section 10. Each event leaves a notice in `tenant_notifications`
(what the panel will list) and, for most of them, an email to the owner:

| kind                    | when                                           | email            |
|-------------------------|------------------------------------------------|------------------|
| `nuevo_comprobante`     | a receipt arrived                              | yes              |
| `pedido_contra_entrega` | the customer confirmed a cash-on-delivery order| yes              |
| `derivacion`            | the assistant handed a conversation to a person| yes              |
| `ventana_cerrada`       | the customer could not be told                 | no               |
| `llm_caido`             | the model failed several times in a row        | yes, once an hour|
| `derivacion_sin_atender` | a handover nobody took in N minutes (007)    | yes              |

The notice is written in the caller's transaction; the email is *not* sent here. It is
queued in `pending_emails` and the caller sends it after the commit, as every other
email of the platform: a message about something that then rolled back is worse than
no message. A failure to mail never fails the sale it is about.

Nothing the simulator does notifies anyone (C-19): its orders are not business.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.orders import Order, PaymentProof
from app.models.tenant import (
    CASH_ORDER,
    CUSTOMER_UNREACHED,
    HANDOFF,
    LLM_DOWN,
    NEW_RECEIPT,
    UNATTENDED,
    Tenant,
)
from app.orders.notices import NotReached
from app.providers.email.base import EmailMessage, EmailProvider
from app.repositories.notification import TenantNotificationRepository
from app.services.activation import owner_of
from app.services.emails import (
    build_cash_order_email,
    build_handoff_email,
    build_llm_down_email,
    build_new_receipt_email,
    build_unattended_email,
)

logger = logging.getLogger(__name__)

# `contracts/api.md` section 10: "sí, máximo 1 por hora".
LLM_DOWN_EMAIL_INTERVAL = timedelta(hours=1)

_CURRENCY_SYMBOLS = {"PEN": "S/", "USD": "US$"}


class TenantAlerts:
    """Implements `OrderNotices`, plus the handover and model-down events."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._clock = clock
        self._notices = TenantNotificationRepository(session, tenant_id)
        # Emails produced since the last commit; the caller sends them after it.
        self.pending_emails: list[EmailMessage] = []

    # ------------------------------------------------------------- order events

    def new_proof(self, order: Order, proof: PaymentProof) -> None:
        """A payment receipt arrived and waits for a person."""
        if order.is_sandbox:
            return
        self._notices.record(
            NEW_RECEIPT,
            {
                "order_id": str(order.id),
                "numero": order.numero,
                "proof_id": str(proof.id),
                "total": str(order.total),
                "moneda": order.moneda,
                "observaciones": list(proof.observaciones),
            },
        )
        self._email(
            lambda to, name: build_new_receipt_email(
                to=to,
                tenant_name=name,
                numero=order.numero,
                total=_money(order),
                panel_url=self._panel("pedidos"),
            )
        )

    def cash_order(self, order: Order) -> None:
        """A cash-on-delivery order waits for a person."""
        if order.is_sandbox:
            return
        self._notices.record(
            CASH_ORDER,
            {
                "order_id": str(order.id),
                "numero": order.numero,
                "total": str(order.total),
                "moneda": order.moneda,
            },
        )
        self._email(
            lambda to, name: build_cash_order_email(
                to=to,
                tenant_name=name,
                numero=order.numero,
                total=_money(order),
                panel_url=self._panel("pedidos"),
            )
        )

    def customer_not_notified(self, order: Order, reason: NotReached) -> None:
        """The decision on an order was saved but the customer was not told.

        No email: the person who just decided is looking at the panel, which is where
        this shows (C-09: "avísale al cliente por otro medio").
        """
        if order.is_sandbox:
            return
        self._notices.record(
            CUSTOMER_UNREACHED,
            {
                "order_id": str(order.id),
                "numero": order.numero,
                "conversation_id": str(order.conversation_id),
                "motivo": reason,
            },
        )

    # ------------------------------------------------------------ other events

    def handoff(
        self, conversation_id: uuid.UUID, *, motivo: str, resumen: str | None = None
    ) -> None:
        """The assistant handed a conversation to a person (`derivar_a_humano`).

        The summary travels in the notice and the email so the person who picks the
        conversation up knows what it was about (TASK-443).
        """
        self._notices.record(
            HANDOFF,
            {
                "conversation_id": str(conversation_id),
                "motivo": motivo,
                "resumen": resumen,
            },
        )
        self._email(
            lambda to, name: build_handoff_email(
                to=to,
                tenant_name=name,
                motivo=motivo,
                resumen=resumen,
                panel_url=self._panel(""),
            )
        )

    def unattended(
        self, conversation_id: uuid.UUID, *, motivo: str, resumen: str | None, minutes: int
    ) -> None:
        """A handover nobody took within the business's minutes (Feature 007, HU-03)."""
        self._notices.record(
            UNATTENDED,
            {"conversation_id": str(conversation_id), "motivo": motivo, "minutos": minutes},
        )
        self._email(
            lambda to, name: build_unattended_email(
                to=to,
                tenant_name=name,
                motivo=motivo,
                resumen=resumen,
                minutes=minutes,
                panel_url=self._panel(f"bandeja?c={conversation_id}"),
            )
        )

    def llm_down(self) -> None:
        """The circuit breaker opened (TASK-445).

        Every opening leaves its notice; the email goes out at most once an hour, so a
        provider that flaps does not fill the owner's inbox. The check and the write are
        serialised per tenant (`lock_kind`), so two workers tripping the breaker at the
        same moment send one email, not two.
        """
        self._notices.lock_kind(LLM_DOWN)
        now = self._clock()
        send_email = not self._notices.emailed_since(LLM_DOWN, now - LLM_DOWN_EMAIL_INTERVAL)
        self._notices.record(LLM_DOWN, {"correo": send_email})
        if send_email:
            self._email(
                lambda to, name: build_llm_down_email(
                    to=to, tenant_name=name, panel_url=self._panel("")
                )
            )

    # ----------------------------------------------------------------- helpers

    def _email(self, build: Callable[[str, str], EmailMessage]) -> None:
        owner = owner_of(self._session, self._tenant_id)
        if owner is None:
            logger.warning(
                "a notice has no owner to email", extra={"tenant_id": str(self._tenant_id)}
            )
            return
        tenant = self._session.get(Tenant, self._tenant_id)
        self.pending_emails.append(build(owner.email, tenant.name if tenant else ""))

    @staticmethod
    def _panel(path: str) -> str:
        base = get_settings().frontend_base_url.rstrip("/")
        return f"{base}/{path}" if path else base


def _money(order: Order) -> str:
    symbol = _CURRENCY_SYMBOLS.get(order.moneda, order.moneda)
    return f"{symbol} {order.total:.2f}"


async def send_emails(provider: EmailProvider | None, messages: Sequence[EmailMessage]) -> None:
    """Deliver the emails a notice produced, after its commit.

    A failure is logged and swallowed: the notice is already recorded and visible in
    the panel, and failing the request or the job over an unreachable mail provider
    would retry work that was applied perfectly.
    """
    if not messages:
        return
    if provider is None:
        logger.warning("no email provider: %d notice email(s) not sent", len(messages))
        return
    for message in messages:
        try:
            await provider.send(message)
        except Exception:  # noqa: BLE001 - a mail outage is not the caller's failure
            logger.exception("could not send a notice email")
