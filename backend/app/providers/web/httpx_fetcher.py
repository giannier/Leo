"""`WebFetcher` backed by httpx (Feature 006, TASK-603).

The only module that opens connections to a tenant's website. Its rules:

* **Public addresses only.** The host is resolved first and every address must
  be global (no loopback, private, link-local, reserved or multicast), so a URL
  like `http://169.254.169.254/` or a name that resolves to `10.0.0.5` is refused
  before a byte is sent. Redirects are followed by hand (at most five), and each
  hop is checked the same way: a public page redirecting inward is refused too.
  Residual risk, stated: a name re-resolving to another address between the check
  and the connection (DNS rebinding) is not prevented here.
* **http and https only**, on their default ports.
* **Bounded:** a timeout per request and a byte cap per page (the body is read in
  pieces and dropped past the cap).
* **Honest:** a user agent that says who reads and why (`USER_AGENT`).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.providers.web.base import (
    USER_AGENT,
    BlockedAddressError,
    FetchedPage,
    WebFetchError,
)

MAX_REDIRECTS = 5
DEFAULT_PORTS = {"http": 80, "https": 443}


class HttpxWebFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_bytes: int = 2_000_000,
        allow_private: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        # Tests point the fetcher at a server on localhost; nothing else should.
        self._allow_private = allow_private
        self._transport = transport

    async def fetch(self, url: str) -> FetchedPage:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"},
            transport=self._transport,
            # An injected transport (tests) must not be bypassed by a proxy from the env.
            trust_env=self._transport is None,
        ) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                await self._check(current)
                try:
                    async with client.stream("GET", current) as response:
                        if response.is_redirect and "location" in response.headers:
                            current = urljoin(current, response.headers["location"])
                            continue
                        body = await self._read(response)
                        return FetchedPage(
                            url=str(response.url),
                            status=response.status_code,
                            content_type=response.headers.get("content-type", "")
                            .split(";")[0]
                            .strip()
                            .lower(),
                            body=body,
                            charset=response.charset_encoding,
                        )
                except httpx.HTTPError as exc:
                    raise WebFetchError(f"no se pudo abrir {current}: {exc}") from exc
        raise WebFetchError(f"demasiadas redirecciones desde {url}")

    async def _read(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise WebFetchError(f"la página pesa más de {self._max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _check(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in DEFAULT_PORTS:
            raise BlockedAddressError(f"solo se leen páginas http o https: {url}")
        if not parts.hostname:
            raise BlockedAddressError(f"la dirección no tiene dominio: {url}")
        default_port = parts.port in (None, DEFAULT_PORTS[parts.scheme])
        if not default_port and not self._allow_private:
            raise BlockedAddressError(f"puerto no permitido: {url}")
        if self._allow_private:
            return
        addresses = await self._addresses(parts.hostname)
        if not addresses:
            raise WebFetchError(f"no existe el dominio {parts.hostname}")
        for raw in addresses:
            address = ipaddress.ip_address(raw)
            if not address.is_global:
                raise BlockedAddressError(f"{parts.hostname} apunta a una dirección no pública")

    async def _addresses(self, host: str) -> list[str]:
        """Every address `host` resolves to (an IP literal resolves to itself)."""
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except OSError:
            return []
        return [str(info[4][0]) for info in infos]
