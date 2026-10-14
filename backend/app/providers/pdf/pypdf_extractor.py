"""`PdfTextExtractor` backed by pypdf.

pypdf reads the text layer a PDF already contains. It does not do OCR, so an
image-only document raises `PdfWithoutTextError` rather than returning
nothing - which is exactly the behaviour plan.md's risk table asks for ("v0
solo extrae texto embebido; PDFs sin texto se marcan `failed` con mensaje
claro").

The only module in the codebase allowed to import `pypdf`.
"""

from __future__ import annotations

import logging
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from app.providers.pdf.base import InvalidPdfError, PdfWithoutTextError

logger = logging.getLogger(__name__)

# Pages are joined with a blank line so the chunker sees a paragraph break
# where a page break was; a sentence split across two pages is rare enough
# that keeping page boundaries is the better default.
PAGE_SEPARATOR = "\n\n"

NO_TEXT_MESSAGE = (
    "This PDF has no extractable text layer. It is most likely a scan or an "
    "export of images. Upload a text-based PDF, or paste the content as a "
    "text source."
)


class PypdfTextExtractor:
    """Extracts the embedded text layer of a PDF."""

    def __init__(self, *, max_pages: int | None = None) -> None:
        """`max_pages` bounds work on a pathological document.

        `None` means no limit. The upload size cap is the primary guard; this
        exists because page count and byte size are only loosely related.
        """
        self._max_pages = max_pages

    def extract_text(self, data: bytes) -> str:
        reader = self._open(data)

        pages = reader.pages
        if self._max_pages is not None and len(pages) > self._max_pages:
            raise InvalidPdfError(
                f"PDF has {len(pages)} pages, more than the {self._max_pages} supported."
            )

        extracted: list[str] = []
        for number, page in enumerate(pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # one bad page must not lose the rest
                # A single malformed content stream is common in the wild.
                # Skipping the page beats failing a 40-page menu over one of
                # them; if every page fails, the no-text check below fires.
                logger.warning("could not extract text from PDF page %d: %s", number, exc)
                continue
            if text.strip():
                extracted.append(text.strip())

        if not extracted:
            raise PdfWithoutTextError(NO_TEXT_MESSAGE)

        return PAGE_SEPARATOR.join(extracted)

    # ------------------------------------------------------- `PdfPages`

    def page_texts(self, data: bytes) -> list[str]:
        """Every page's text layer, `""` where there is none (a scanned page)."""
        texts: list[str] = []
        for number, page in enumerate(self._open(data).pages, start=1):
            try:
                texts.append((page.extract_text() or "").strip())
            except Exception as exc:  # one bad page must not lose the rest
                logger.warning("could not extract text from PDF page %d: %s", number, exc)
                texts.append("")
        return texts

    def page_pdf(self, data: bytes, index: int) -> bytes:
        """One page as a standalone PDF: what a vision model is shown, page by page."""
        writer = PdfWriter()
        writer.add_page(self._open(data).pages[index])
        out = BytesIO()
        writer.write(out)
        return out.getvalue()

    def _open(self, data: bytes) -> PdfReader:
        try:
            reader = PdfReader(BytesIO(data))
        except PyPdfError as exc:
            raise InvalidPdfError(f"the file could not be read as a PDF: {exc}") from exc
        except Exception as exc:  # pypdf also raises plain ValueError/OSError
            raise InvalidPdfError(f"the file could not be read as a PDF: {exc}") from exc

        if reader.is_encrypted:
            # An empty owner password is the usual case for "protected"
            # documents that are really just flagged read-only.
            try:
                opened = reader.decrypt("")
            except Exception as exc:  # depends on the installed crypto backend
                raise InvalidPdfError(
                    f"the PDF is encrypted and could not be opened: {exc}"
                ) from exc
            if not opened:
                raise InvalidPdfError(
                    "The PDF is password-protected. Remove the password and upload it again."
                )
        return reader
