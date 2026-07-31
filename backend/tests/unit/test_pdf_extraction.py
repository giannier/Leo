"""Contract tests for the `PdfTextExtractor` port and its pypdf adapter.

Real PDF bytes, assembled here rather than checked in as fixtures: the
important case - a page with graphics and no text layer, i.e. a scan - has to
be a genuinely valid PDF, otherwise the test proves the adapter rejects
garbage rather than that it detects a missing text layer.
"""

from __future__ import annotations

import pytest

from app.providers.pdf import (
    InvalidPdfError,
    MockPdfTextExtractor,
    PdfTextExtractor,
    PdfWithoutTextError,
    PypdfTextExtractor,
)

# --------------------------------------------------------------- PDF builder

_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _assemble(objects: list[bytes]) -> bytes:
    """Wrap numbered objects in a PDF file with a correct xref table."""
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def _pdf(*pages: bytes) -> bytes:
    """A PDF whose pages draw the given content streams."""
    page_numbers = [3 + index * 2 for index in range(len(pages))]
    kids = " ".join(f"{number} 0 R" for number in page_numbers)

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
    ]
    for index, stream in enumerate(pages):
        contents = 4 + index * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {2 + len(pages) * 2 + 1} 0 R >> >> "
            f"/Contents {contents} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(_FONT)
    return _assemble(objects)


def _text_stream(text: str) -> bytes:
    return f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")


# A page that draws a filled rectangle: valid, renderable, and with no text
# layer at all. This is what a scanned menu looks like to a parser.
_IMAGE_ONLY_STREAM = b"0.2 0.4 0.6 rg 72 72 468 648 re f"


# -------------------------------------------------------------------- tests


class TestTheMockSatisfiesThePort:
    def test_mock_is_a_pdf_text_extractor(self) -> None:
        assert isinstance(MockPdfTextExtractor(text="x"), PdfTextExtractor)

    def test_real_adapter_is_a_pdf_text_extractor(self) -> None:
        assert isinstance(PypdfTextExtractor(), PdfTextExtractor)

    def test_a_mock_with_no_text_still_refuses_to_report_success(self) -> None:
        """The mock must not be able to fake the bug the port exists to stop."""
        with pytest.raises(PdfWithoutTextError):
            MockPdfTextExtractor().extract_text(b"%PDF-1.4")


class TestExtraction:
    def test_reads_the_text_layer(self) -> None:
        extracted = PypdfTextExtractor().extract_text(_pdf(_text_stream("Menu del dia S/ 18")))
        assert "Menu del dia S/ 18" in extracted

    def test_reads_every_page(self) -> None:
        document = _pdf(_text_stream("Primera pagina"), _text_stream("Segunda pagina"))
        extracted = PypdfTextExtractor().extract_text(document)
        assert "Primera pagina" in extracted
        assert "Segunda pagina" in extracted

    def test_pages_are_separated_by_a_paragraph_break(self) -> None:
        """So the chunker treats a page boundary as a structural boundary."""
        document = _pdf(_text_stream("Uno"), _text_stream("Dos"))
        assert "\n\n" in PypdfTextExtractor().extract_text(document)

    def test_a_page_with_text_survives_a_sibling_without_one(self) -> None:
        document = _pdf(_IMAGE_ONLY_STREAM, _text_stream("Solo esta pagina tiene texto"))
        assert "Solo esta pagina tiene texto" in PypdfTextExtractor().extract_text(document)


class TestTheScannedDocumentCase:
    """The most common real upload, and the one that must never look fine."""

    def test_a_page_with_graphics_and_no_text_raises(self) -> None:
        with pytest.raises(PdfWithoutTextError):
            PypdfTextExtractor().extract_text(_pdf(_IMAGE_ONLY_STREAM))

    def test_every_page_without_text_raises(self) -> None:
        with pytest.raises(PdfWithoutTextError):
            PypdfTextExtractor().extract_text(_pdf(_IMAGE_ONLY_STREAM, _IMAGE_ONLY_STREAM))

    def test_the_message_tells_the_tenant_what_to_do(self) -> None:
        """`error_msg` is shown in the panel; "failed" alone is useless."""
        with pytest.raises(PdfWithoutTextError) as caught:
            PypdfTextExtractor().extract_text(_pdf(_IMAGE_ONLY_STREAM))
        message = str(caught.value).lower()
        assert "no extractable text" in message
        assert "scan" in message

    def test_it_is_never_reported_as_empty_success(self) -> None:
        """Returning "" here is the bug; the exception type is the fix."""
        extractor = PypdfTextExtractor()
        with pytest.raises(PdfWithoutTextError):
            extractor.extract_text(_pdf(_IMAGE_ONLY_STREAM))


class TestUnreadableFiles:
    @pytest.mark.parametrize(
        "data",
        [b"", b"not a pdf at all", b"%PDF-1.4\ntruncated"],
        ids=["empty", "not-a-pdf", "truncated"],
    )
    def test_unopenable_bytes_raise_invalid_pdf(self, data: bytes) -> None:
        with pytest.raises(InvalidPdfError):
            PypdfTextExtractor().extract_text(data)

    def test_a_page_budget_is_enforced(self) -> None:
        document = _pdf(_text_stream("Uno"), _text_stream("Dos"), _text_stream("Tres"))
        with pytest.raises(InvalidPdfError, match="pages"):
            PypdfTextExtractor(max_pages=2).extract_text(document)

    def test_both_failures_share_a_base_type(self) -> None:
        """The worker catches one type and marks the source `failed`."""
        from app.providers.pdf import PdfError

        assert issubclass(InvalidPdfError, PdfError)
        assert issubclass(PdfWithoutTextError, PdfError)
