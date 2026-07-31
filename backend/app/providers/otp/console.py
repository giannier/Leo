"""Development `OtpSender` that writes the code to the application log.

The only implementation that exists today, because no delivery channel has
been chosen: WhatsApp arrives with Feature 002 and no SMS provider is
approved. It refuses to run outside development, so a deployment that forgets
to configure a real channel fails at startup instead of silently printing
customers' one-time codes into a production log aggregator.
"""

from __future__ import annotations

import logging

from app.providers.otp.base import OtpDeliveryError

logger = logging.getLogger(__name__)


class ConsoleOtpSender:
    """Logs the code. Development only."""

    def __init__(self, *, environment: str) -> None:
        if environment not in ("development", "test"):
            raise OtpDeliveryError(
                "ConsoleOtpSender writes one-time codes to the log and must never "
                f"run in {environment!r}. Configure a real OTP channel first."
            )

    async def send(self, *, phone_e164: str, code: str) -> None:
        logger.warning(
            "DEV ONLY - OTP for %s is %s. This is logged because no delivery "
            "channel is configured yet (WhatsApp arrives in Feature 002).",
            phone_e164,
            code,
        )
