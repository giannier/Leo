"""The `OtpSender` port.

Delivering a one-time code is a separate concern from generating and checking
it, and the channel is genuinely undecided: WhatsApp Business is not connected
until Feature 002, and no SMS provider has been approved. Putting the send
behind a port means the OTP flow can be finished, tested and reviewed now, and
the real channel becomes one adapter later with nothing above it changing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class OtpDeliveryError(RuntimeError):
    """The code could not be handed to the delivery channel."""


@runtime_checkable
class OtpSender(Protocol):
    async def send(self, *, phone_e164: str, code: str) -> None:
        """Deliver `code` to `phone_e164`.

        Implementations must never log or echo `code`. It is the credential.
        """
        ...
