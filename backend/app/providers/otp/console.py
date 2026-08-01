"""Development `OtpSender` that writes the code to the application log.

The only implementation that exists today, because no delivery channel has
been chosen: WhatsApp arrives with Feature 002 and no SMS provider is
approved. It refuses to run outside development, so a deployment that forgets
to configure a real channel fails at startup instead of silently printing
customers' one-time codes into a production log aggregator.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.providers.otp.base import OtpDeliveryError

logger = logging.getLogger(__name__)


class ConsoleOtpSender:
    """Logs the code. Development only."""

    def __init__(self, *, environment: str, outbox_path: str | None = None) -> None:
        if environment not in ("development", "test"):
            raise OtpDeliveryError(
                "ConsoleOtpSender writes one-time codes to the log and must never "
                f"run in {environment!r}. Configure a real OTP channel first."
            )
        # The same append-only JSONL the console email provider writes to.
        # A log line is fine for a person watching a terminal; the end-to-end
        # suite has to *read* the code, and the database only ever stores its
        # hash - so the delivery is the one place the plaintext exists.
        #
        # Safe by construction: this class refuses to exist outside
        # development, so no configuration turns a deployment into one
        # writing customers' one-time codes to disk.
        self._outbox = Path(outbox_path) if outbox_path else None

    async def send(self, *, phone_e164: str, code: str) -> None:
        logger.warning(
            "DEV ONLY - OTP for %s is %s. This is logged because no delivery "
            "channel is configured yet (WhatsApp arrives in Feature 002).",
            phone_e164,
            code,
        )

        if self._outbox is not None:
            self._outbox.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "id": f"otp-{uuid.uuid4()}",
                "at": datetime.now(UTC).isoformat(),
                "to": [phone_e164],
                "subject": "Codigo de verificacion",
                "links": [],
                "text": f"Tu codigo de verificacion es {code}",
            }
            with self._outbox.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
