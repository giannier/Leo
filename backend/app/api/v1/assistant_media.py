"""The media library: `/assistant/media` (Feature 004 §3, TASK-436)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, SessionDep, get_storage_provider
from app.api.errors import ApiError, error_responses
from app.models.media import CLAVE_PATTERN, MediaAsset
from app.process.service import DEFAULT_CACHE
from app.providers.storage.base import StorageError, StorageProvider
from app.repositories.media import MediaAssetRepository
from app.repositories.product import ProductRepository
from app.repositories.sales_config import PromotionRepository
from app.services.media_library import MAX_UPLOAD, MediaLibrary, MediaRejected

router = APIRouter(prefix="/assistant/media", tags=["assistant"])
StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (
        403,
        "`email_not_verified`, `no_active_tenant`, `tenant_access_denied` or `insufficient_role`",
    ),
)
PREVIEW_SECONDS = 600
_CLAVE = re.compile(CLAVE_PATTERN)

Clave = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=CLAVE_PATTERN)
]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
WhenToUse = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300)]
Caption = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1024)]


class MediaResponse(BaseModel):
    id: uuid.UUID
    clave: str
    tipo: str
    titulo: str
    cuando_usar: str
    caption: str | None
    product_id: uuid.UUID | None
    promotion_id: uuid.UUID | None
    is_active: bool
    filename: str
    size_bytes: int
    preview_url: str | None
    preview_expires_at: datetime | None


class MediaList(BaseModel):
    items: list[MediaResponse]


class MediaPatch(BaseModel):
    clave: Clave | None = None
    titulo: Title | None = None
    cuando_usar: WhenToUse | None = None
    caption: Caption | None = None
    product_id: uuid.UUID | None = None
    promotion_id: uuid.UUID | None = None
    is_active: bool | None = Field(default=None)


def _response(asset: MediaAsset, storage: StorageProvider) -> MediaResponse:
    try:
        url: str | None = storage.presigned_url(asset.storage_key, expires_in=PREVIEW_SECONDS)
    except StorageError:
        url = None
    return MediaResponse(
        id=asset.id,
        clave=asset.clave,
        tipo=asset.tipo,
        titulo=asset.titulo,
        cuando_usar=asset.cuando_usar,
        caption=asset.caption,
        product_id=asset.product_id,
        promotion_id=asset.promotion_id,
        is_active=asset.is_active,
        filename=asset.filename,
        size_bytes=asset.size_bytes,
        preview_url=url,
        preview_expires_at=datetime.now(UTC) + timedelta(seconds=PREVIEW_SECONDS) if url else None,
    )


def _rejected(exc: MediaRejected) -> ApiError:
    return ApiError(status_code=422, error=exc.code, message=str(exc))


def _check_links(
    session: SessionDep, tenant_id: uuid.UUID, product_id: uuid.UUID | None, promo: uuid.UUID | None
) -> None:
    if product_id is not None and ProductRepository(session, tenant_id).get(product_id) is None:
        raise ApiError(status_code=422, error="unknown_product", message="Unknown product.")
    if promo is not None and PromotionRepository(session, tenant_id).get(promo) is None:
        raise ApiError(status_code=422, error="unknown_promotion", message="Unknown promotion.")


async def _read(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ApiError(status_code=422, error="file_too_large", message="The file is too large.")
    return data


def _asset(session: SessionDep, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> MediaAsset:
    asset = MediaAssetRepository(session, tenant_id).get(asset_id)
    if asset is None:
        raise ApiError(status_code=404, error="not_found", message="Not found.")
    return asset


@router.get("", response_model=MediaList, responses=error_responses(*AUTH_RESPONSES))
def list_media(
    principal: ConfigurationManagerDep, session: SessionDep, storage: StorageDep
) -> MediaList:
    assets = MediaAssetRepository(session, principal.tenant_id).list()
    return MediaList(items=[_response(a, storage) for a in sorted(assets, key=lambda a: a.clave)])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=MediaResponse,
    responses=error_responses(
        *AUTH_RESPONSES,
        (
            422,
            "`unsupported_file`, `file_too_large`, `empty_file`, `clave_taken`, "
            "`invalid_clave`, `unknown_product` or `unknown_promotion`",
        ),
        (503, "`storage_unavailable`"),
    ),
)
async def upload_media(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
    clave: Annotated[str, Form()],
    titulo: Annotated[str, Form()],
    cuando_usar: Annotated[str, Form()],
    caption: Annotated[str | None, Form()] = None,
    product_id: Annotated[uuid.UUID | None, Form()] = None,
    promotion_id: Annotated[uuid.UUID | None, Form()] = None,
) -> MediaResponse:
    clave = clave.strip().lower()
    if not _CLAVE.match(clave):
        raise ApiError(
            status_code=422,
            error="invalid_clave",
            message="La clave: minúsculas, números y guion bajo, de 2 a 40.",
        )
    if not 5 <= len(cuando_usar.strip()) <= 300 or not 1 <= len(titulo.strip()) <= 120:
        raise ApiError(
            status_code=422, error="validation_error", message="Revisa el título y el uso."
        )
    _check_links(session, principal.tenant_id, product_id, promotion_id)
    data = await _read(file)
    try:
        asset = MediaLibrary(session, principal.tenant_id, storage).upload(
            data,
            file.filename or "",
            clave=clave,
            titulo=titulo.strip(),
            cuando_usar=cuando_usar.strip(),
            caption=(caption or "").strip()[:1024] or None,
            product_id=product_id,
            promotion_id=promotion_id,
        )
    except MediaRejected as exc:
        raise _rejected(exc) from exc
    except StorageError as exc:
        raise ApiError(
            status_code=503, error="storage_unavailable", message="Could not store the file."
        ) from exc
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return _response(asset, storage)


@router.patch(
    "/{asset_id}",
    response_model=MediaResponse,
    responses=error_responses(
        *AUTH_RESPONSES, (404, "`not_found`"), (422, "`clave_taken` or `unknown_product`")
    ),
)
def update_media(
    asset_id: uuid.UUID,
    body: MediaPatch,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
) -> MediaResponse:
    asset = _asset(session, principal.tenant_id, asset_id)
    values = body.model_dump(exclude_unset=True)
    _check_links(session, principal.tenant_id, values.get("product_id"), values.get("promotion_id"))
    new_clave = values.get("clave")
    if (
        new_clave
        and new_clave != asset.clave
        and MediaAssetRepository(session, principal.tenant_id).by_clave(new_clave)
    ):
        raise ApiError(status_code=422, error="clave_taken", message="Esa clave ya existe.")
    for key, value in values.items():
        if key in ("clave", "titulo", "cuando_usar", "is_active") and value is None:
            continue
        setattr(asset, key, value)
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return _response(asset, storage)


@router.post(
    "/{asset_id}/file",
    response_model=MediaResponse,
    responses=error_responses(
        *AUTH_RESPONSES,
        (404, "`not_found`"),
        (422, "`unsupported_file`, `file_too_large` or `type_changed`"),
    ),
)
async def replace_media_file(
    asset_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> MediaResponse:
    asset = _asset(session, principal.tenant_id, asset_id)
    data = await _read(file)
    try:
        MediaLibrary(session, principal.tenant_id, storage).replace_file(
            asset, data, file.filename or ""
        )
    except MediaRejected as exc:
        raise _rejected(exc) from exc
    except StorageError as exc:
        raise ApiError(
            status_code=503, error="storage_unavailable", message="Could not store the file."
        ) from exc
    session.commit()
    return _response(asset, storage)


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH_RESPONSES, (404, "`not_found`")),
)
def delete_media(
    asset_id: uuid.UUID,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    storage: StorageDep,
) -> Response:
    MediaLibrary(session, principal.tenant_id, storage).delete(
        _asset(session, principal.tenant_id, asset_id)
    )
    session.commit()
    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
