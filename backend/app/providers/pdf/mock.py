"""In-memory `PdfTextExtractor` for tests.

Scripted, so a test can produce the scanned-PDF case without carrying a
binary fixture around: `MockPdfTextExtractor(fail_with=PdfWithoutTextError(...))`.
"""

from __future__ import annotations

from app.providers.pdf.base import PdfError, PdfWithoutTextError


class MockPdfTextExtractor:
    """Returns queued text, or raises the failure it was given."""

    def __init__(self, *, text: str = "", fail_with: PdfError | None = None) -> None:
        self._text = text
        self._fail_with = fail_with
        self.calls: list[bytes] = []

    def extract_text(self, data: bytes) -> str:
        self.calls.append(data)
        if self._fail_with is not None:
            raise self._fail_with
        if not self._text.strip():
            # Mirrors the real adapter: never hand back an empty success.
            raise PdfWithoutTextError("mock extractor was configured with no text")
        return self._text
