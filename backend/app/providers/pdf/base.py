"""The `PdfTextExtractor` port.

PDF parsing is a third-party library, so it lives behind a port like every
other external dependency (Constitution: "SDKs de terceros solo dentro de su
adaptador"). Two things follow from that:

* The ingestion worker never imports `pypdf`. It asks this interface for
  text and handles the typed errors below.
* Feature 006 adds OCR for scanned documents by writing another adapter, not
  by editing the worker.

`PdfWithoutTextError` is a separate type on purpose. A scanned menu - an
image of text with no text layer - is the single most common real upload,
and returning `""` for it would let the pipeline record an empty success:
`indexed`, zero chunks, an agent that knows nothing and a tenant who was
told everything worked.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class PdfError(RuntimeError):
    """The document could not be turned into text."""


class InvalidPdfError(PdfError):
    """The bytes are not a PDF this library can open.

    Corrupt, truncated, or password-protected. Distinct from
    `PdfWithoutTextError` because the remedy differs: re-export the file
    versus run it through OCR.
    """


class PdfWithoutTextError(PdfError):
    """The PDF opened, but carries no text layer.

    Almost always a scan or an export of images. Never a transient failure,
    so retrying is pointless - the source must be marked `failed` with an
    explanation the tenant can act on.
    """


@runtime_checkable
class PdfTextExtractor(Protocol):
    def extract_text(self, data: bytes) -> str:
        """Return the document's text.

        Raises `InvalidPdfError` if the file cannot be opened and
        `PdfWithoutTextError` if it opens but yields nothing. Never returns
        an empty or whitespace-only string.
        """
        ...
