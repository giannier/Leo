"""v1 WhatsApp adapter: an admin connects the number by hand.

Stores and validates what Giannier pastes in after doing the YCloud work
manually. It sends nothing, because v1 does not send - Feature 002 adds the
API integration behind this same port.

The validation is structural, not a proof. Confirming that a number really
sends requires sending, and a v1 that silently claimed otherwise would be
worse than one that is honest about the limit.
"""

from __future__ import annotations

from app.providers.whatsapp.base import InvalidCredentials, NumberCredentials

# The identifiers YCloud issues per number. Named here rather than accepted
# blindly so a paste that is missing half of them fails at the admin's screen
# rather than at the first customer message.
YCLOUD_REQUIRED_KEYS = ("waba_id", "phone_number_id", "api_key")

MIN_VALUE_LENGTH = 6


class ManualYCloudProvider:
    """Validates and stores credentials an admin obtained by hand."""

    @property
    def name(self) -> str:
        return "ycloud"

    def validate(self, credentials: NumberCredentials) -> None:
        if credentials.provider != self.name:
            raise InvalidCredentials(
                f"expected provider {self.name!r}, got {credentials.provider!r}"
            )

        missing = [key for key in YCLOUD_REQUIRED_KEYS if not credentials.values.get(key)]
        if missing:
            raise InvalidCredentials(f"missing credentials: {', '.join(missing)}")

        # A placeholder left in a form is the realistic failure here, not a
        # malformed token: somebody tabs through the fields and saves "TODO".
        short = [
            key
            for key in YCLOUD_REQUIRED_KEYS
            if len(credentials.values[key].strip()) < MIN_VALUE_LENGTH
        ]
        if short:
            raise InvalidCredentials(f"implausibly short credentials: {', '.join(short)}")
