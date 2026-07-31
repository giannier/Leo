"""The `WhatsAppProvider` port.

v1 connects numbers **manually**: a customer files a request, and Giannier
does the provider-side work by hand before loading the resulting credentials
(Clarification 007, and the risk table's "cuello de botella operativo").

The port exists anyway, and that is deliberate. Feature 002 replaces the
manual step with YCloud's API, and the code above this line must not notice.
Writing the adapter now, while there is only one trivial implementation, is
what keeps the seam honest - a seam introduced after the fact is shaped by
whatever the first implementation happened to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class WhatsAppError(RuntimeError):
    """The provider could not complete the operation."""


class InvalidCredentials(WhatsAppError):
    """The credentials supplied do not describe a usable number."""


@dataclass(frozen=True)
class NumberCredentials:
    """What a provider needs to send on a number's behalf.

    Deliberately a free-form mapping rather than named fields: YCloud, Meta
    and any future gateway name these differently, and inventing a canonical
    shape now would mean guessing at three vendors from one.
    """

    provider: str
    values: dict[str, str]


@runtime_checkable
class WhatsAppProvider(Protocol):
    @property
    def name(self) -> str: ...

    def validate(self, credentials: NumberCredentials) -> None:
        """Raise `InvalidCredentials` if these could not possibly work.

        Cheap structural checks only. Whether the number actually sends is
        proved by sending, which v1 does not do.
        """
        ...
