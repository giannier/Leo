"""Product catalogue endpoints (TASK-011).

The `/tenant/products` section of `contracts/api.md`.

Import is two calls: upload reports what the parser made of the file, and a
second call creates the rows. A customer with five hundred products cannot
know what happened to their spreadsheet until they see it, and there is no
undo for four hundred bad rows.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, MemberDep, RedisDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.services.catalog import CatalogService, PreviewNotFound
from app.services.csv_import import (
    MAX_BYTES,
    TEMPLATE_CSV,
    ImportError_,
)

router = APIRouter(prefix="/tenant/products", tags=["products"])

ProductName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


class ProductRequest(BaseModel):
    name: ProductName
    description: ShortText | None = None
    # Decimal, never float: money is Decimal everywhere (Constitution), and a
    # float would make 19.99 arrive as 19.989999999999998.
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    # None means this product does not track stock at all, which is different
    # from tracking it and having none.
    stock: int | None = Field(default=None, ge=0)
    category: ShortText | None = None
    is_active: bool = True


class ProductPatch(BaseModel):
    name: ProductName | None = None
    description: ShortText | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    stock: int | None = Field(default=None, ge=0)
    category: ShortText | None = None
    is_active: bool | None = None


class ProductResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    price: Decimal
    stock: int | None
    category: str | None
    is_active: bool


class ProductPage(BaseModel):
    items: list[ProductResponse]
    # What lets a client render "showing 50 of 312" and know another page
    # exists; a bare list cannot express either.
    total: int
    limit: int
    offset: int


class InvalidRowResponse(BaseModel):
    row: int
    reason: str


class ImportPreviewResponse(BaseModel):
    preview_id: uuid.UUID
    valid_rows: int
    invalid_rows: list[InvalidRowResponse]


class ImportConfirmResponse(BaseModel):
    created: int


def _as_response(product: object) -> ProductResponse:
    return ProductResponse.model_validate(product, from_attributes=True)


def _not_found() -> ApiError:
    """404 for anything this tenant does not own.

    Never 403: a different answer for "exists but is not yours" confirms the
    id exists (contracts/api.md says so explicitly for this resource).
    """
    return ApiError(status_code=status.HTTP_404_NOT_FOUND, error="not_found", message="Not found.")


@router.get("", summary="List products", responses=error_responses(*AUTH_RESPONSES))
async def list_products(
    principal: MemberDep,
    session: SessionDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductPage:
    """Soft-deleted products are never listed."""
    items, total = CatalogService(session, principal.tenant_id).list_products(
        search=search, category=category, is_active=is_active, limit=limit, offset=offset
    )
    return ProductPage(
        items=[_as_response(item) for item in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/template.csv",
    summary="Download an import template",
    response_class=Response,
    responses=error_responses(*AUTH_RESPONSES),
)
async def download_template(principal: MemberDep) -> Response:
    """A file the customer can fill in, with Spanish headers and two examples.

    Declared before `/{product_id}` so the literal path is matched first;
    otherwise `template.csv` is read as a product id and 422s.
    """
    return Response(
        content=TEMPLATE_CSV,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plantilla-productos.csv"'},
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
    responses=error_responses((400, "`validation_error`"), *AUTH_RESPONSES),
)
async def create_product(
    payload: ProductRequest, principal: ConfigurationManagerDep, session: SessionDep
) -> ProductResponse:
    product = CatalogService(session, principal.tenant_id).create(**payload.model_dump())
    return _as_response(product)


@router.get(
    "/{product_id}",
    summary="Read one product",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
async def get_product(
    product_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ProductResponse:
    product = CatalogService(session, principal.tenant_id).get(product_id)
    if product is None:
        raise _not_found()
    return _as_response(product)


@router.put(
    "/{product_id}",
    summary="Update a product",
    responses=error_responses((400, "`validation_error`"), (404, "`not_found`"), *AUTH_RESPONSES),
)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductPatch,
    principal: ConfigurationManagerDep,
    session: SessionDep,
) -> ProductResponse:
    """Partial: fields not sent keep their current value."""
    values = payload.model_dump(exclude_unset=True)
    product = CatalogService(session, principal.tenant_id).update(product_id, **values)
    if product is None:
        raise _not_found()
    return _as_response(product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a product",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
async def delete_product(
    product_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> None:
    """Soft delete. Past conversations reference products by id, and a hard
    delete would rewrite what the agent said last week."""
    if not CatalogService(session, principal.tenant_id).soft_delete(product_id):
        raise _not_found()


@router.post(
    "/import",
    summary="Upload a catalogue and preview what would be imported",
    responses=error_responses(
        (400, "`invalid_file`, `empty_file` or `missing_columns`"),
        (413, "`file_too_large`"),
        *AUTH_RESPONSES,
    ),
)
async def import_preview(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    redis: RedisDep,
    file: Annotated[UploadFile, File()],
) -> ImportPreviewResponse:
    """Parses and reports. Creates nothing until `/confirm`."""
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise ApiError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            error="file_too_large",
            message=f"The file exceeds the {MAX_BYTES // (1024 * 1024)} MB limit.",
        )

    service = CatalogService(session, principal.tenant_id, redis=redis)
    try:
        preview = await service.stage_import(raw)
    except ImportError_ as exc:
        reason = str(exc)
        raise ApiError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if reason == "file_too_large"
            else status.HTTP_400_BAD_REQUEST,
            error=reason.split(":")[0],
            message=_explain(reason),
        ) from exc

    return ImportPreviewResponse(
        preview_id=preview.preview_id,
        valid_rows=preview.valid_rows,
        invalid_rows=[
            InvalidRowResponse(row=row.row, reason=row.reason) for row in preview.invalid_rows
        ],
    )


@router.post(
    "/import/{preview_id}/confirm",
    status_code=status.HTTP_201_CREATED,
    summary="Create the products a preview accepted",
    responses=error_responses((404, "`preview_not_found`"), *AUTH_RESPONSES),
)
async def import_confirm(
    preview_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    redis: RedisDep,
) -> ImportConfirmResponse:
    """The preview is consumed, so a double click cannot import twice."""
    service = CatalogService(session, principal.tenant_id, redis=redis)
    try:
        created = await service.confirm_import(preview_id)
    except PreviewNotFound as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            error="preview_not_found",
            message="That preview has expired or was already confirmed. Upload the file again.",
        ) from exc
    return ImportConfirmResponse(created=created)


def _explain(reason: str) -> str:
    if reason.startswith("missing_columns:"):
        missing = reason.split(":", 1)[1]
        return f"The file is missing required columns: {missing}."
    return {
        "empty_file": "The file is empty.",
        "file_too_large": "The file is too large.",
    }.get(reason, "The file could not be read.")
