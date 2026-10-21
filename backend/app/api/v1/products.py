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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.api.deps import (
    ConfigurationManagerDep,
    MemberDep,
    RedisDep,
    SessionDep,
    get_storage_provider,
)
from app.api.errors import ApiError, error_responses
from app.models.business import BUNDLE_MAX_SIZE, BUNDLE_MIN_SIZE, Product
from app.models.media import MediaAsset
from app.process.service import DEFAULT_CACHE
from app.providers.storage.base import StorageError, StorageProvider
from app.services.catalog import CatalogService, PreviewNotFound
from app.services.csv_import import (
    MAX_BYTES,
    TEMPLATE_CSV,
    ImportError_,
)
from app.services.media_library import MAX_UPLOAD, MediaRejected
from app.services.product_photos import ProductPhotos
from app.services.product_tags import TagsInvalid, clean_tags

router = APIRouter(prefix="/tenant/products", tags=["products"])
StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]
PREVIEW_SECONDS = 600

ProductName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
SaleMode = Literal["precio_fijo", "cotizacion"]
PricePeriod = Literal["unico", "mes", "ano"]
BundleSize = Annotated[int, Field(ge=BUNDLE_MIN_SIZE, le=BUNDLE_MAX_SIZE)]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)


Tags = Annotated[list[Annotated[str, StringConstraints(max_length=200)]], Field(max_length=50)]


def _clean(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    try:
        return clean_tags(value)
    except TagsInvalid as exc:
        raise ValueError(str(exc)) from exc


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
    # Fit for sale (Feature 004). A bundle is a product with a `bundle_size`.
    modo_venta: SaleMode = "precio_fijo"
    periodo: PricePeriod = "unico"
    es_digital: bool = False
    bundle_size: BundleSize | None = None
    bundle_permite_libres: bool = False
    destacado: bool = False
    # Up to ten labels; the agent also finds the product by them (Feature 018).
    tags: Tags = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return _clean(value) or []


class ProductPatch(BaseModel):
    name: ProductName | None = None
    description: ShortText | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    stock: int | None = Field(default=None, ge=0)
    category: ShortText | None = None
    is_active: bool | None = None
    modo_venta: SaleMode | None = None
    periodo: PricePeriod | None = None
    es_digital: bool | None = None
    # Unlike the other new fields, `bundle_size` may be sent as null: that turns
    # a package back into a single product.
    bundle_size: BundleSize | None = None
    bundle_permite_libres: bool | None = None
    destacado: bool | None = None
    tags: Tags | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            raise ValueError("must not be null")
        return _clean(value)

    @field_validator("modo_venta", "periodo", "es_digital", "bundle_permite_libres", "destacado")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        """These columns are NOT NULL: omit the field to leave it as it is."""
        if value is None:
            raise ValueError("must not be null")
        return value


class ProductResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    price: Decimal
    stock: int | None
    category: str | None
    is_active: bool
    modo_venta: SaleMode
    periodo: PricePeriod
    es_digital: bool
    bundle_size: int | None
    bundle_permite_libres: bool
    destacado: bool
    tags: list[str]
    # The main photo (the first by order), for the catalogue list; None without photos.
    foto_url: str | None = None


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


def _url(asset: MediaAsset | None, storage: StorageProvider | None) -> str | None:
    if asset is None or storage is None:
        return None
    try:
        return storage.presigned_url(asset.storage_key, expires_in=PREVIEW_SECONDS)
    except StorageError:
        return None


def _as_response(
    product: object, photo: MediaAsset | None = None, storage: StorageProvider | None = None
) -> ProductResponse:
    response = ProductResponse.model_validate(product, from_attributes=True)
    response.foto_url = _url(photo, storage)
    return response


class PhotoResponse(BaseModel):
    id: uuid.UUID
    url: str | None
    filename: str
    size_bytes: int


class PhotoList(BaseModel):
    items: list[PhotoResponse]


class PhotoOrder(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=50)


def _photos(items: list[MediaAsset], storage: StorageProvider) -> PhotoList:
    return PhotoList(
        items=[
            PhotoResponse(
                id=a.id, url=_url(a, storage), filename=a.filename, size_bytes=a.size_bytes
            )
            for a in items
        ]
    )


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
    storage: StorageDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    is_active: bool | None = None,
    tag: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductPage:
    """Soft-deleted products are never listed. `search` matches the name or a tag."""
    items, total = CatalogService(session, principal.tenant_id).list_products(
        search=search, category=category, is_active=is_active, tag=tag, limit=limit, offset=offset
    )
    main = ProductPhotos(session, principal.tenant_id, storage).main_photos(items)
    return ProductPage(
        items=[_as_response(item, main.get(item.id), storage) for item in items],
        total=total,
        limit=limit,
        offset=offset,
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


@router.post(
    "/{product_id}/duplicate",
    status_code=status.HTTP_201_CREATED,
    summary="Copy a product, inactive, without its photos",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
async def duplicate_product(
    product_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> ProductResponse:
    copy = CatalogService(session, principal.tenant_id).duplicate(product_id)
    if copy is None:
        raise _not_found()
    return _as_response(copy)


def _product(session: SessionDep, tenant_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    product = CatalogService(session, tenant_id).get(product_id)
    if product is None:
        raise _not_found()
    return product


@router.get(
    "/{product_id}/photos",
    summary="The product's photos, in order",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
def list_photos(
    product_id: uuid.UUID, principal: MemberDep, session: SessionDep, storage: StorageDep
) -> PhotoList:
    product = _product(session, principal.tenant_id, product_id)
    return _photos(ProductPhotos(session, principal.tenant_id, storage).of(product), storage)


@router.post(
    "/{product_id}/photos",
    status_code=status.HTTP_201_CREATED,
    summary="Add a photo (at most 10)",
    responses=error_responses(
        (404, "`not_found`"),
        (
            422,
            "`too_many_photos`, `not_an_image`, `unsupported_file`, `file_too_large` or "
            "`empty_file`",
        ),
        (503, "`storage_unavailable`"),
        *AUTH_RESPONSES,
    ),
)
async def add_photo(
    product_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> PhotoList:
    product = _product(session, principal.tenant_id, product_id)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ApiError(status_code=422, error="file_too_large", message="La foto es muy pesada.")
    photos = ProductPhotos(session, principal.tenant_id, storage)
    try:
        photos.add(product, data, file.filename or "")
    except MediaRejected as exc:
        raise ApiError(status_code=422, error=exc.code, message=str(exc)) from exc
    except StorageError as exc:
        raise ApiError(
            status_code=503, error="storage_unavailable", message="No pudimos guardar la foto."
        ) from exc
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return _photos(photos.of(product), storage)


@router.put(
    "/{product_id}/photos/order",
    summary="Set the order of the photos; the first is the main one",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
def order_photos(
    product_id: uuid.UUID,
    body: PhotoOrder,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
) -> PhotoList:
    product = _product(session, principal.tenant_id, product_id)
    ordered = ProductPhotos(session, principal.tenant_id, storage).reorder(product, body.ids)
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return _photos(ordered, storage)


@router.delete(
    "/{product_id}/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a photo",
    responses=error_responses((404, "`not_found`"), *AUTH_RESPONSES),
)
def delete_photo(
    product_id: uuid.UUID,
    photo_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
) -> None:
    product = _product(session, principal.tenant_id, product_id)
    if not ProductPhotos(session, principal.tenant_id, storage).remove(product, photo_id):
        raise _not_found()
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
