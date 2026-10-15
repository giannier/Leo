"""The `WebFetcher` port (Feature 006, TASK-603).

Reading the business's website is an outbound HTTP call to an address a tenant
typed, so it lives behind a port like every other third-party call, and the
adapter is where the network rules are enforced (only public addresses, a byte
cap, a timeout, an honest user agent). The crawler above it decides *what* to
read; it never opens a socket itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

USER_AGENT = "LeoBot/1.0 (+https://zentek360.com/leo; lee la web del negocio a pedido del negocio)"
# The product token robots.txt rules are matched against.
ROBOTS_AGENT = "LeoBot"


class WebFetchError(RuntimeError):
    """The page could not be fetched (network, timeout, too large, refused)."""


class BlockedAddressError(WebFetchError):
    """The URL points somewhere a tenant must not make the platform reach.

    Private, loopback, link-local and other non-public addresses: the cloud
    metadata endpoint, the database, the internal network. Raised before any
    connection, and again for every redirect hop.
    """


@dataclass(frozen=True)
class FetchedPage:
    url: str  # after redirects
    status: int
    content_type: str
    body: bytes
    charset: str | None = None


@runtime_checkable
class WebFetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage:
        """GET `url`, following redirects, or raise `WebFetchError`."""
        ...
