"""In-memory `OtpSender` for tests.

Records every delivery so a test can assert the code that was actually sent,
which is the only way to drive the verify step end to end.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.otp.base import OtpDeliveryError


@dataclass(frozen=True)
class SentOtp:
    phone_e164: str
    code: str


class MockOtpSender:
    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.sent: list[SentOtp] = []
        self._fail_with = fail_with

    async def send(self, *, phone_e164: str, code: str) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        if not phone_e164:
            raise OtpDeliveryError("no destination phone")
        self.sent.append(SentOtp(phone_e164=phone_e164, code=code))

    @property
    def last(self) -> SentOtp:
        if not self.sent:
            raise AssertionError("no OTP was sent")
        return self.sent[-1]
