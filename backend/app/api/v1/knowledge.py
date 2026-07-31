"""Knowledge base endpoints (TASK-012).

Implements the `§Conocimiento` section of
`leo-sdd/features/001-onboarding/contracts/api.md`:

    GET    /tenant/knowledge       sources + usage against the plan
    POST   /tenant/knowledge       202, indexing continues in a worker
    DELETE /tenant/knowledge/{id}  204, chunks removed with it

`POST` accepts two content types on one path, as the contract specifies:
JSON for `faq`/`policy`/`text`, and `multipart/form-data` for a PDF. FastAPI
cannot declare two request bodies for one operation, so the body is read from
the request and dispatched on its content type; `openapi_extra` documents
both shapes so the generated frontend client still sees them.

Every route is authenticated through `app/api/deps.py`, and the tenant is the
one the caller's access token resolves to - never a path or body parameter.
Reading is open to any active member; adding and removing sources is a
configuration change, so Clarification 008 keeps it to `owner` and
`tenant_admin` and leaves the `operator` with the inbox.

Request and response models live here rather than in a `schemas/` package,
matching `auth.py` and `health.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, StringConstraints, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import (
    ConfigurationManagerDep,
    MemberDep,
    get_db,
    get_job_queue,
    get_storage_provider,
)
from app.api.errors import VALIDATION_ERROR, ApiError, error_responses
from app.core.queue import JobQueue
from app.providers.storage.base import StorageError, StorageProvider
from app.services.knowledge import (
    EmptyContentError,
    IndexingUnavailableError,
    InvalidUploadError,
    KnowledgeService,
    KnowledgeUsage,
    PdfTooLargeError,
    PlanUnavailableError,
    RagLimitExceededError,
    SourceNotFoundError,
    UnsupportedSourceTypeError,
)

router = APIRouter(prefix="/tenant/knowledge", tags=["knowledge"])

# An inline paste this long is already pathological, and an unbounded body is
# a denial-of-service surface. The *plan* budget (`max_rag_chars`) is enforced
# separately and can be larger than this; a single source simply cannot be.
MAX_INLINE_CHARS = 1_000_000

MULTIPART = "multipart/form-data"

TitleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
BodyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INLINE_CHARS)
]


# ------------------------------------------------------------------ schemas


class TextSourceRequest(BaseModel):
    """JSON body of `POST /tenant/knowledge` for an inline source."""

    type: Literal["faq", "policy", "text"]
    title: TitleText
    content: BodyText


class CreateSourceResponse(BaseModel):
    """`202` body: the source exists, indexing has been queued."""

    id: uuid.UUID
    status: str


class KnowledgeUsageResponse(BaseModel):
    """Consumption against the tenant's plan (Clarification 011)."""

    chars_used: int
    chars_limit: int
    files_used: int
    files_limit: int


class KnowledgeSourceResponse(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    status: str
    char_count: int
    # Populated when `status` is `failed`; the sentence shown to the tenant.
    error_msg: str | None = None
    created_at: datetime


class KnowledgeListResponse(BaseModel):
    sources: list[KnowledgeSourceResponse]
    usage: KnowledgeUsageResponse


SessionDep = Annotated[Session, Depends(get_db)]
StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]
QueueDep = Annotated[JobQueue, Depends(get_job_queue)]

# Every route shares the same authentication chain, so the statuses it can
# answer with are written once and spread into each operation's own set.
_UNAUTHENTICATED = (401, "`not_authenticated`, `token_expired` or `invalid_token`")
_NOT_A_MEMBER = (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`")
_NOT_ALLOWED = (403, f"{_NOT_A_MEMBER[1]}, or `insufficient_role` for an `operator`")


# Documents the two accepted bodies, which FastAPI cannot infer from a
# hand-parsed request.
_CREATE_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "application/json": {"schema": TextSourceRequest.model_json_schema()},
        MULTIPART: {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "The PDF to index.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Shown in the panel. Defaults to the file name.",
                    },
                },
            }
        },
    },
}


# ----------------------------------------------------------------- routes


@router.get(
    "",
    response_model=KnowledgeListResponse,
    summary="List knowledge sources and the plan budget they consume",
    responses=error_responses(_UNAUTHENTICATED, _NOT_A_MEMBER),
)
async def list_knowledge(session: SessionDep, principal: MemberDep) -> KnowledgeListResponse:
    """Every live source, plus usage against `max_rag_chars` / `max_knowledge_files`.

    Open to any active member: an operator working the inbox needs to see
    what the agent is answering from, and reading changes nothing.
    """
    service = KnowledgeService(session, principal.tenant_id)
    try:
        usage = service.usage()
    except PlanUnavailableError as exc:
        raise _plan_unavailable(exc) from exc

    return KnowledgeListResponse(
        sources=[
            KnowledgeSourceResponse(
                id=source.id,
                type=source.type,
                title=source.title,
                status=source.status,
                char_count=source.char_count,
                error_msg=source.error_msg,
                created_at=source.created_at,
            )
            for source in service.list_sources()
        ],
        usage=_usage_response(usage),
    )


@router.post(
    "",
    response_model=CreateSourceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Add a knowledge source and queue it for indexing",
    openapi_extra={"requestBody": _CREATE_REQUEST_BODY},
    responses=error_responses(
        (400, "`validation_error` or `invalid_pdf`"),
        _UNAUTHENTICATED,
        _NOT_ALLOWED,
        (413, "`rag_limit_exceeded` (with current usage in `fields`) or `pdf_too_large`"),
        (503, "`indexing_unavailable` or `plan_unavailable`"),
    ),
)
async def create_knowledge_source(
    request: Request,
    session: SessionDep,
    principal: ConfigurationManagerDep,
    storage: StorageDep,
    queue: QueueDep,
) -> CreateSourceResponse:
    """Accept a source. Extraction, chunking and embedding happen in the worker.

    Answers `202`, not `201`: the row exists but is not usable yet. The
    tenant polls `GET /tenant/knowledge` for `indexed` or `failed`.

    What the agent answers from is configuration, so this is closed to the
    `operator` role (Clarification 008).
    """
    service = KnowledgeService(session, principal.tenant_id, storage=storage, queue=queue)
    content_type = request.headers.get("content-type", "")

    try:
        if content_type.startswith(MULTIPART):
            result = await _ingest_upload(request, service)
        else:
            result = await _ingest_json(request, service)
    except RagLimitExceededError as exc:
        # The contract requires the current usage to come back with the 413.
        raise ApiError(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            error="rag_limit_exceeded",
            message=exc.message,
            fields=exc.usage.as_fields(),
        ) from exc
    except PdfTooLargeError as exc:
        raise ApiError(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            error="pdf_too_large",
            message=f"This PDF is {exc.size} bytes; the limit is {exc.limit}.",
            fields={"file": f"at most {exc.limit} bytes"},
        ) from exc
    except (InvalidUploadError, UnsupportedSourceTypeError) as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error="invalid_pdf" if isinstance(exc, InvalidUploadError) else VALIDATION_ERROR,
            message=str(exc),
        ) from exc
    except EmptyContentError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=VALIDATION_ERROR,
            message=str(exc),
        ) from exc
    except PlanUnavailableError as exc:
        raise _plan_unavailable(exc) from exc
    except IndexingUnavailableError as exc:
        # The source was not kept: nothing would ever pick it up.
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error="indexing_unavailable",
            message=str(exc),
        ) from exc
    except StorageError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error="storage_unavailable",
            message="The file could not be stored. Please try again.",
        ) from exc

    return CreateSourceResponse(id=result.id, status=result.status)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a knowledge source and its embeddings",
    responses=error_responses(
        _UNAUTHENTICATED,
        _NOT_ALLOWED,
        (404, "`not_found`"),
    ),
)
async def delete_knowledge_source(
    source_id: uuid.UUID, session: SessionDep, principal: ConfigurationManagerDep
) -> None:
    """Retire the source and delete its chunks.

    Closed to the `operator` role, like `POST`: deleting a document silently
    changes every answer the agent gives afterwards.

    A source belonging to another tenant answers `404`, never `403`: a
    different status would confirm that the id exists. The role check runs
    first, so an operator is refused before the lookup either way.
    """
    try:
        KnowledgeService(session, principal.tenant_id).delete(source_id)
    except SourceNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            error="not_found",
            message="No knowledge source with that id.",
        ) from exc


# ---------------------------------------------------------------- internals


async def _ingest_json(request: Request, service: KnowledgeService) -> Any:
    """Parse and ingest an inline faq/policy/text source."""
    try:
        raw = await request.json()
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=VALIDATION_ERROR,
            message="The request body is not valid JSON.",
        ) from exc

    try:
        payload = TextSourceRequest.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    return await service.ingest_text(
        source_type=payload.type, title=payload.title, content=payload.content
    )


async def _ingest_upload(request: Request, service: KnowledgeService) -> Any:
    """Parse and ingest a multipart PDF upload."""
    form = await request.form()
    upload = form.get("file")
    if not hasattr(upload, "read"):
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=VALIDATION_ERROR,
            message="The upload needs a `file` part containing the PDF.",
            fields={"file": "required"},
        )

    data = await upload.read()  # type: ignore[union-attr]
    filename = getattr(upload, "filename", None) or "document.pdf"

    supplied = form.get("title")
    title = supplied if isinstance(supplied, str) and supplied.strip() else filename

    return await service.ingest_pdf(title=title, data=data)


def _usage_response(usage: KnowledgeUsage) -> KnowledgeUsageResponse:
    return KnowledgeUsageResponse(
        chars_used=usage.chars_used,
        chars_limit=usage.chars_limit,
        files_used=usage.files_used,
        files_limit=usage.files_limit,
    )


def _plan_unavailable(exc: PlanUnavailableError) -> ApiError:
    """A misconfigured deployment, not a bad request.

    Answering 4xx would send the tenant off to fix something on their side
    that they cannot reach.
    """
    return ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        error="plan_unavailable",
        message="The plan limits for this account could not be read.",
    )


def _validation_error(exc: ValidationError) -> ApiError:
    """Render a hand-parsed body's failure like the framework's own.

    `errors.py` translates FastAPI's `RequestValidationError`; this body is
    validated manually, so the same envelope has to be produced here rather
    than letting a pydantic error escape as a 500.
    """
    fields: dict[str, str] = {}
    for error in exc.errors():
        path = ".".join(str(part) for part in error["loc"]) or "body"
        fields.setdefault(path, str(error["msg"]))
    return ApiError(
        status_code=status.HTTP_400_BAD_REQUEST,
        error=VALIDATION_ERROR,
        message="The request payload is invalid.",
        fields=fields,
    )
