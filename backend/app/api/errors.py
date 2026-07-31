"""The single error shape every endpoint returns.

`contracts/api.md` specifies `{"error", "message", "fields"?}` for every
failure, where `error` is a stable machine code the frontend switches on and
`message` is a human fallback. FastAPI's own default is `{"detail": ...}`,
and its validation failures are 422, so both are translated here rather than
being handled endpoint by endpoint - one place to change, and no endpoint can
forget.

`message` is written in English: it is a developer-facing fallback. Copy the
tenant actually reads is the frontend's, keyed off the stable `error` code.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

VALIDATION_ERROR = "validation_error"
INTERNAL_ERROR = "internal_error"


class ErrorResponse(BaseModel):
    """The documented failure body, so it appears in the OpenAPI schema.

    The frontend's API client is generated from that schema, so an error
    shape that only exists at runtime would not reach it.
    """

    error: str
    message: str
    fields: dict[str, str] | None = None


class ApiError(Exception):
    """A failure the client is meant to act on.

    Carries the whole envelope, so the endpoint that raises it decides the
    status code and the stable code together and there is no mapping table
    to drift out of sync.
    """

    def __init__(
        self,
        *,
        status_code: int,
        error: str,
        message: str,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.fields = fields


def error_body(*, error: str, message: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
    """Build the response body. `fields` is omitted when there is none."""
    body: dict[str, Any] = {"error": error, "message": message}
    if fields:
        body["fields"] = fields
    return body


def _status_slug(status_code: int) -> str:
    """A stable code for errors raised without one, e.g. `not_found`."""
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:  # pragma: no cover - only for non-standard codes
        return "http_error"
    return phrase.lower().replace(" ", "_").replace("-", "_")


def _field_path(location: tuple[int | str, ...]) -> str:
    """Render a pydantic error location as a dotted field path.

    The first element is the request part (`body`, `query`, ...), which the
    client already knows; dropping it makes `fields` keyed by the names the
    client actually sent.
    """
    parts = [str(part) for part in location[1:]] or [str(part) for part in location]
    return ".".join(parts)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render an `ApiError` as the documented envelope."""
    if not isinstance(exc, ApiError):  # pragma: no cover - registered for this type
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(error=exc.error, message=exc.message, fields=exc.fields),
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a request validation failure as `400 validation_error`.

    The contract says 400, not FastAPI's default 422.
    """
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered for this type
        raise exc
    fields: dict[str, str] = {}
    for error in exc.errors():
        fields.setdefault(_field_path(tuple(error["loc"])), str(error["msg"]))
    return JSONResponse(
        status_code=400,
        content=error_body(
            error=VALIDATION_ERROR,
            message="The request payload is invalid.",
            fields=fields,
        ),
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render framework-raised HTTP errors (404, 405, ...) in the envelope."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered for this type
        raise exc
    detail = exc.detail if isinstance(exc.detail, str) else _status_slug(exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(error=_status_slug(exc.status_code), message=detail),
        headers=getattr(exc, "headers", None),
    )


def error_responses(*documented: tuple[int, str]) -> dict[int | str, dict[str, Any]]:
    """Build a route's `responses=`, all sharing the envelope model.

    Pass `(status, description)` pairs; the description is where the stable
    `error` codes for that status belong, since one status can carry several
    (409 is both `email_taken` and `phone_in_active_trial`).
    """
    return {
        status_code: {"model": ErrorResponse, "description": description}
        for status_code, description in documented
    }


def _use_documented_error_schema(app: FastAPI) -> None:
    """Drop FastAPI's automatic 422 from the schema.

    The API answers 400 for a malformed payload - `validation_error_handler`
    sees to that - so a 422 left in the schema would make the generated
    client expect a status this service never sends.
    """

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                operation.get("responses", {}).pop("422", None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def install_error_contract(app: FastAPI) -> None:
    """Make every failure - runtime and documented - use the envelope."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    _use_documented_error_schema(app)
