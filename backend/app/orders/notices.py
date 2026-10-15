"""What the orders code tells the business about (Feature 004, TASK-430/431).

A port, so the code that records a receipt, confirms an order or tries to reach the
customer does not know how the business is told (a panel notice, an email: TASK-431).
Every method is fire-and-forget from the caller's side: telling the business must
never fail the sale it is about.
"""

from __future__ import annotations

import uuid
from typing import Literal, Protocol

from app.models.orders import Order, PaymentProof

# Why the customer could not be told: Meta's 24-hour window is closed, or the
# provider refused or the number is gone.
NotReached = Literal["ventana_cerrada", "envio_fallido"]


class OrderNotices(Protocol):
    def new_proof(self, order: Order, proof: PaymentProof) -> None:
        """A payment receipt arrived and waits for a person."""
        ...

    def cash_order(self, order: Order) -> None:
        """The customer confirmed an order to pay on delivery; it waits for a person."""
        ...

    def customer_not_notified(self, order: Order, reason: NotReached) -> None:
        """The operator's decision was saved but the customer was not told."""
        ...

    def handoff(self, conversation_id: uuid.UUID, *, motivo: str, resumen: str | None) -> None:
        """The assistant handed the conversation to a person (`derivar_a_humano`)."""
        ...
