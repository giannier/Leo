"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_contract
from app.api.security_headers import SecurityHeadersMiddleware
from app.api.v1 import router as v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging

DOCS_URL = "/docs"
REDOC_URL = "/redoc"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        docs_url=DOCS_URL,
        redoc_url=REDOC_URL,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Added last, so it wraps everything above it and no response - including
    # a CORS preflight answered by the middleware itself, or an error rendered
    # by the envelope handlers - can leave without the headers.
    app.add_middleware(
        SecurityHeadersMiddleware,
        # Only where TLS terminates in front of the application. In
        # development this would pin `localhost` to HTTPS in the developer's
        # browser, breaking every other project on the same machine.
        hsts=settings.is_deployed,
        csp_exempt_paths=frozenset({DOCS_URL, REDOC_URL}),
    )

    # Before the routers, so every failure - including the ones FastAPI
    # raises itself - comes back in the envelope the API contract documents.
    install_error_contract(app)

    app.include_router(v1_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
