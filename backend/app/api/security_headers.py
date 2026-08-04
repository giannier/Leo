"""Security headers added to every response.

None of these change what the API *answers*; they change what a browser is
willing to do with the answer. They are cheap, they are the same on every
route, and the only way to be sure no endpoint forgets one is to add them in
one place, above the router - exactly like the error envelope next door.

Why each one is here:

- `X-Content-Type-Options: nosniff` - stops a browser from deciding for itself
  that a JSON body is really HTML or JavaScript. That guess is the whole
  mechanism behind content-sniffing XSS, and a tenant controls plenty of the
  strings this API echoes back (business name, product names, knowledge
  titles).
- `X-Frame-Options: DENY` and `frame-ancestors 'none'` - the API is never
  meant to be framed. Both are sent because the first is what older browsers
  obey and the second is what current ones do.
- `Referrer-Policy: no-referrer` - request paths here carry tenant and
  resource identifiers. A referrer header would hand them to whatever host a
  redirect leads to.
- `Content-Security-Policy: default-src 'none'` - a JSON API loads nothing.
  Saying so means a response that somehow does render as a document can still
  not pull a script, a frame or an image from anywhere.
- `Cross-Origin-Resource-Policy: same-site` - keeps another origin from
  embedding a response as a subresource and reading it back through a side
  channel.
- `Permissions-Policy` - denies the device APIs outright. Nothing here needs a
  camera, a microphone or a location, so nothing framed or opened from a
  response should be able to ask.
- `Strict-Transport-Security` - only where TLS actually exists. Sending it
  from the development stack would pin `localhost` to HTTPS in the developer's
  browser for a year, and every other project on that machine would inherit
  the damage.

`Server` is stripped on the way out: naming the exact server and version tells
an attacker which advisories to try and tells a legitimate client nothing.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# One year. Shorter values are honoured, but every preload list requires at
# least this, and a policy that expires next week protects nobody.
HSTS_MAX_AGE_SECONDS = 31_536_000

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
    "microphone=(), payment=(), usb=()"
)

# Sent on every response, in every environment.
BASE_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-site",
    "permissions-policy": PERMISSIONS_POLICY,
}

HSTS_HEADER = f"max-age={HSTS_MAX_AGE_SECONDS}; includeSubDomains"


class SecurityHeadersMiddleware:
    """Adds the headers above to every response.

    Written as raw ASGI rather than on top of `BaseHTTPMiddleware` because
    this only needs to touch the response *start* message. `BaseHTTPMiddleware`
    would put a task group and a queue in front of every request to do the
    same work, and it changes how background tasks and streaming responses
    behave. Nothing here reads or rewrites a body.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        hsts: bool = False,
        csp_exempt_paths: frozenset[str] = frozenset(),
    ) -> None:
        self._app = app
        self._hsts = hsts
        # The interactive documentation, when it is enabled. Swagger UI and
        # ReDoc are real HTML pages that load their own script and stylesheet,
        # so `default-src 'none'` renders them as a blank screen. They are the
        # one place in this service that legitimately needs to load something,
        # and they exist only where `create_app` decided to expose them.
        self._csp_exempt = csp_exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in BASE_HEADERS.items():
                    headers[name] = value
                if path not in self._csp_exempt:
                    headers["content-security-policy"] = CONTENT_SECURITY_POLICY
                if self._hsts:
                    headers["strict-transport-security"] = HSTS_HEADER
                # Uvicorn sets this; there is nothing to gain from announcing it.
                del headers["server"]
            await send(message)

        await self._app(scope, receive, send_with_headers)
