"""Serves a file kept in Postgres to whoever holds a valid link.

Public on purpose: `<img src>` in the panel and WhatsApp downloading media cannot
send a login, so the link itself is the credential (see
`app/providers/storage/links.py`). Everything here follows from that:

- **Every refusal is the same 404** - a forged link, an expired one, a file that
  was deleted - so the response tells a guesser nothing, and none of them reaches
  the database except the last.
- **It never answers 500.** This URL is reachable by anyone; a storage failure is
  a 503 the client can retry, not a stack trace.
- **It never trusts what it serves.** Anything that is not an image, audio, video
  or PDF is a download and not a page. `nosniff`, `no-referrer`, the CSP and the
  frame bans are the app-wide ones from `SecurityHeadersMiddleware`, which wraps
  every response: this route adds none of its own, so there is one place that
  decides them and no route can quietly weaken them.
- **Cheap to repeat.** The ETag is the file's checksum (a repeat visit is a 304
  with no bytes), and `Range` is honoured, because Safari will not play audio or
  video without it and because a seek to the end of a long file should read one
  chunk, not all of them.

Only the Postgres store has links to serve. With the S3 store configured this
route answers 404, and S3 serves its own presigned URLs.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response, StreamingResponse

from app.api.deps import get_storage_provider
from app.api.errors import ApiError
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.storage.postgres_provider import PostgresStorageProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]

# Customers' payment receipts are somebody's private paperwork: the browser may
# show them, but should not keep a copy on disk. Everything else may be reused
# until its link would have stopped working anyway.
NO_STORE_KINDS = frozenset({"proofs"})

_INLINE_PREFIXES = ("image/", "audio/", "video/")
_INLINE_TYPES = frozenset({"application/pdf"})
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_MAX_DIGITS = 18  # more than any file size, and below Python's int-parsing limit

Unsatisfiable = Literal["unsatisfiable"]
UNSATISFIABLE: Unsatisfiable = "unsatisfiable"


def _not_found() -> ApiError:
    return ApiError(status_code=status.HTTP_404_NOT_FOUND, error="not_found", message="Not found.")


def _unavailable() -> ApiError:
    return ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        error="storage_unavailable",
        message="The file could not be read right now. Try again in a moment.",
    )


def _etag_matches(header: str | None, sha256: str) -> bool:
    if not header:
        return False
    for candidate in header.split(","):
        value = candidate.strip()
        if value == "*":
            return True
        if value.startswith("W/"):
            value = value[2:]
        if value.strip('"') == sha256:
            return True
    return False


def _select_range(
    range_header: str | None, if_range: str | None, etag: str, size: int
) -> tuple[int, int] | Unsatisfiable | None:
    """The single byte range to send, `UNSATISFIABLE`, or `None` for the whole file.

    Anything this does not understand - several ranges, a malformed header, an
    `If-Range` that no longer matches - is answered with the whole file, which RFC
    9110 allows and every client copes with. Only a well-formed range that lies
    outside the file is an error.
    """
    if not range_header:
        return None
    if if_range and if_range.strip() != etag:
        return None
    match = _RANGE.fullmatch(range_header.strip())
    if match is None:
        return None
    first, last = match.groups()
    if len(first) > _MAX_DIGITS or len(last) > _MAX_DIGITS or (not first and not last):
        return None
    if not first:  # "-N": the last N bytes
        count = int(last)
        if count == 0 or size == 0:
            return UNSATISFIABLE
        return max(size - count, 0), size - 1
    start = int(first)
    if start >= size:
        return UNSATISFIABLE
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def _guarded(chunks: Iterator[bytes], key: str) -> Iterator[bytes]:
    """Log a storage failure mid-download, then let it abort the connection.

    Headers are already sent by then, so there is no status left to change; a cut
    connection is what tells the client the file is incomplete.
    """
    try:
        yield from chunks
    except StorageError:
        logger.exception("storage failed while serving %s", key)
        raise


@router.api_route(
    "/{token}",
    methods=["GET", "HEAD"],
    summary="A stored file, for the holder of a signed link",
    include_in_schema=False,
)
def serve_file(token: str, request: Request, storage: StorageDep) -> Response:
    if not isinstance(storage, PostgresStorageProvider):
        raise _not_found()
    link = storage.resolve_link(token)
    if link is None:
        raise _not_found()
    try:
        info = storage.stat(link.key)
    except StorageError as exc:
        logger.exception("storage failed reading the catalogue for a link")
        raise _unavailable() from exc
    if info is None:
        raise _not_found()

    kind = link.key.split("/")[2]
    etag = f'"{info.sha256}"'
    inline = info.content_type.startswith(_INLINE_PREFIXES) or info.content_type in _INLINE_TYPES
    headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Cache-Control": (
            "private, no-store"
            if kind in NO_STORE_KINDS
            else f"private, max-age={max(0, link.expires_at - int(time.time()))}"
        ),
        "Content-Disposition": "inline" if inline else 'attachment; filename="download"',
    }

    if _etag_matches(request.headers.get("if-none-match"), info.sha256):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    selected = _select_range(
        request.headers.get("range"), request.headers.get("if-range"), etag, info.size_bytes
    )
    if selected == UNSATISFIABLE:
        headers["Content-Range"] = f"bytes */{info.size_bytes}"
        return Response(status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE, headers=headers)

    chunks: Iterator[bytes]
    if selected is None:
        status_code, length = status.HTTP_200_OK, info.size_bytes
        chunks = storage.stream(info.key)
    else:
        start, end = selected
        status_code, length = status.HTTP_206_PARTIAL_CONTENT, end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{info.size_bytes}"
        chunks = storage.stream_range(info.key, start, end)
    headers["Content-Length"] = str(length)

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers, media_type=info.content_type)
    return StreamingResponse(
        _guarded(chunks, info.key),
        status_code=status_code,
        headers=headers,
        media_type=info.content_type,
    )
