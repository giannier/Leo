"""Every response carries the hardening headers, including the failures.

The point of adding them above the router is that no endpoint can forget one,
so the assertions below deliberately sample the awkward paths - a 404 the
framework raises, a CORS preflight the middleware answers itself, the
documentation page that needs an exemption - rather than one happy route.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import install_error_contract
from app.api.security_headers import (
    BASE_HEADERS,
    CONTENT_SECURITY_POLICY,
    HSTS_HEADER,
    SecurityHeadersMiddleware,
)
from app.main import DOCS_URL


def _client(app: FastAPI) -> AsyncClient:
    # `raise_app_exceptions=False` so a 500 can be observed as a response.
    # Starlette re-raises after rendering one, which httpx would otherwise
    # surface as the original exception and never show the body.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


def _hardened(*, hsts: bool) -> FastAPI:
    """A minimal app carrying only the middleware under test."""
    app = FastAPI()
    install_error_contract(app)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("the connection string is postgresql://leo:leo@db/leo")

    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)
    return app


class TestTheHeadersAreAlwaysThere:
    async def test_a_successful_response_carries_all_of_them(self) -> None:
        async with _client(_hardened(hsts=False)) as client:
            response = await client.get("/ok")

        assert response.status_code == 200
        for name, value in BASE_HEADERS.items():
            assert response.headers[name] == value
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY

    async def test_an_error_response_carries_them_too(self, app: FastAPI) -> None:
        """A 404 is rendered by an exception handler, not by a route. If the
        headers were added per-endpoint this is exactly where they would be
        missing."""
        async with _client(app) as client:
            response = await client.get("/api/v1/nope")

        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"

    async def test_a_cors_preflight_carries_them(self, app: FastAPI) -> None:
        """The CORS middleware answers a preflight itself, without ever
        reaching the router."""
        async with _client(app) as client:
            response = await client.options(
                "/api/v1/auth/login",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                },
            )

        assert response.status_code == 200
        assert response.headers["referrer-policy"] == "no-referrer"

    async def test_the_server_banner_is_removed(self, app: FastAPI) -> None:
        async with _client(app) as client:
            response = await client.get("/api/v1/health")

        assert "server" not in response.headers


class TestStrictTransportSecurity:
    async def test_it_is_absent_where_there_is_no_tls(self) -> None:
        """Sending it from the development stack pins `localhost` to HTTPS in
        the developer's browser for a year, and every other project on that
        machine inherits the damage."""
        async with _client(_hardened(hsts=False)) as client:
            response = await client.get("/ok")

        assert "strict-transport-security" not in response.headers

    async def test_it_is_sent_where_tls_terminates(self) -> None:
        async with _client(_hardened(hsts=True)) as client:
            response = await client.get("/ok")

        assert response.headers["strict-transport-security"] == HSTS_HEADER


class TestTheContentSecurityPolicy:
    async def test_the_documentation_is_exempt(self, app: FastAPI) -> None:
        """Swagger UI is a real HTML page that loads its own script and
        stylesheet. Under `default-src 'none'` it renders as a blank screen."""
        async with _client(app) as client:
            response = await client.get(DOCS_URL)

        assert response.status_code == 200
        assert "content-security-policy" not in response.headers
        # The rest still applies - only the policy that would break the page
        # is lifted.
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_the_api_itself_is_not_exempt(self, app: FastAPI) -> None:
        async with _client(app) as client:
            response = await client.get("/api/v1/health")

        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


class TestAnUnhandledFailure:
    async def test_it_answers_the_documented_envelope(self) -> None:
        async with _client(_hardened(hsts=False)) as client:
            response = await client.get("/boom")

        assert response.status_code == 500
        assert response.json() == {
            "error": "internal_error",
            "message": "The server could not complete the request.",
        }

    async def test_it_tells_the_caller_nothing_about_the_cause(self) -> None:
        """A 500 is by definition the case nobody reviewed beforehand, so the
        exception text is assumed to carry a connection string, a file path or
        a provider's raw response."""
        async with _client(_hardened(hsts=False)) as client:
            response = await client.get("/boom")

        assert "postgresql" not in response.text
        assert "RuntimeError" not in response.text
