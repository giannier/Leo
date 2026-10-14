"""OCR of scanned PDFs (Feature 006, TASK-602).

The PDFs here are built from real JPEG scans (`tests/fixtures/knowledge/`): each
page is an image and nothing else, exactly what a phone scanner produces. The
model is scripted, except in the last case, which reads the fixture with the real
vision model (`-m evals`, needs `OPENAI_API_KEY`).
"""

# The ingestion module's fixtures are imported and then requested by name, which
# ruff reads as a redefinition.
# ruff: noqa: F401, F811

from __future__ import annotations

import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.providers.llm import LLMError, MockLLMProvider
from app.providers.pdf import PypdfTextExtractor
from app.providers.storage import MockStorageProvider
from app.workers.knowledge_indexer import (
    SCANNED_ILLEGIBLE,
    SCANNED_WITHOUT_OCR,
    IndexingOutcome,
    index_source,
)
from tests.integration.test_knowledge_ingestion import (
    RecordingQueue,
    _post_pdf,
    _source,
    account,
    knowledge,
    llm,
    plan,
    queue,
    storage,
    tenant,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"
MENU_JPEG = (FIXTURES / "menu-escaneado.jpg").read_bytes()
BLURRED_JPEG = (FIXTURES / "pagina-borrosa.jpg").read_bytes()
MENU_TEXT = (
    "Panadería Doña Rosa\nHorario de atención\nLunes a sábado de 7:00 a 21:00.\n"
    "Delivery\nRepartimos en Surco, Barranco y Chorrillos."
)


def _jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """Width and height from the JPEG's start-of-frame marker."""
    at = 2
    while at < len(jpeg):
        marker, length = jpeg[at + 1], int.from_bytes(jpeg[at + 2 : at + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            height = int.from_bytes(jpeg[at + 5 : at + 7], "big")
            width = int.from_bytes(jpeg[at + 7 : at + 9], "big")
            return width, height
        at += 2 + length
    raise ValueError("not a JPEG with a frame header")


def scanned_pdf(*pages: bytes | str) -> bytes:
    """A PDF whose pages are JPEG images (bytes) or, for a mixed document, text (str)."""
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids: list[int] = []
    for page in pages:
        page_number = len(objects) + 1
        kids.append(page_number)
        if isinstance(page, bytes):
            width, height = _jpeg_size(page)
            image_number, content_number = page_number + 1, page_number + 2
            stream = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /XObject << /Im0 {image_number} 0 R >> >> "
                f"/Contents {content_number} 0 R >>".encode()
            )
            objects.append(
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                f"/Length {len(page)} >>\nstream\n".encode()
                + page
                + b"\nendstream"
            )
        else:
            content_number = page_number + 1
            stream = f"BT /F1 14 Tf 72 700 Td ({page}) Tj ET".encode("latin-1")
            objects.append(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 FONTREF >> >> "
                + f"/Contents {content_number} 0 R >>".encode()
            )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    font_number = len(objects) + 1
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects = [o.replace(b"FONTREF", f"{font_number} 0 R".encode()) for o in objects]
    refs = " ".join(f"{k} 0 R" for k in kids)
    objects[1] = f"<< /Type /Pages /Kids [{refs}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


async def _ingest(
    client: AsyncClient,
    db: Session,
    tenant_id: uuid.UUID,
    data: bytes,
    *,
    llm: Any,
    storage: MockStorageProvider,
    queue: RecordingQueue,
    **overrides: Any,
) -> IndexingOutcome:
    created = await _post_pdf(client, data, title="Carta escaneada")
    assert created.status_code == 202, created.text
    settings = get_settings().model_copy(update=overrides) if overrides else None
    return await index_source(
        db,
        tenant_id,
        queue.last_source_id,
        llm=llm,
        storage=storage,
        pdf_extractor=PypdfTextExtractor(),
        settings=settings,
    )


class _CannotRead:
    """A provider with embeddings and no document reading (`DocumentReader` absent)."""

    embedding_model = "mock-embed"
    chat_model = "mock-chat"

    def __init__(self) -> None:
        self._inner = MockLLMProvider()

    async def embed(self, texts: Any, *, input_type: Any = "passage") -> list[list[float]]:
        return await self._inner.embed(texts, input_type=input_type)

    async def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise LLMError("unused")


class TestScannedPages:
    def test_the_fixture_really_is_a_scan(self) -> None:
        texts = PypdfTextExtractor().page_texts(scanned_pdf(MENU_JPEG, BLURRED_JPEG))
        assert texts == ["", ""]

    async def test_readable_pages_are_indexed_and_unreadable_ones_reported(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        llm.document_responses = [
            {"legible": True, "texto": MENU_TEXT},
            {"legible": False, "texto": ""},
        ]

        outcome = await _ingest(
            knowledge,
            db,
            tenant,
            scanned_pdf(MENU_JPEG, BLURRED_JPEG),
            llm=llm,
            storage=storage,
            queue=queue,
        )

        assert outcome.succeeded, outcome
        source = _source(db, queue.last_source_id)
        assert source.char_count == len(MENU_TEXT)
        ocr = (source.report or {})["ocr"]
        assert ocr["leidas"] == [1] and ocr["ilegibles"] == [2]
        assert ocr["sin_leer_por_limite"] == [] and ocr["tokens_entrada"] == 1800
        # Each page went to the model on its own.
        assert [len(PdfReader(BytesIO(call)).pages) for call in llm.document_calls] == [1, 1]
        embedded = " ".join(t for call in llm.embed_calls for t in call.texts)
        assert "Surco" in embedded

    async def test_only_the_scanned_pages_of_a_mixed_document_are_read(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        llm.document_responses = [{"legible": True, "texto": MENU_TEXT}]

        outcome = await _ingest(
            knowledge,
            db,
            tenant,
            scanned_pdf("Carta de bebidas: chicha morada 5 soles, emoliente 3 soles.", MENU_JPEG),
            llm=llm,
            storage=storage,
            queue=queue,
        )

        assert outcome.succeeded
        assert len(llm.document_calls) == 1
        embedded = " ".join(t for call in llm.embed_calls for t in call.texts)
        assert embedded.index("chicha morada") < embedded.index("Surco")

    async def test_pages_over_the_limit_are_reported_not_read(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        llm.document_responses = [{"legible": True, "texto": MENU_TEXT}]

        outcome = await _ingest(
            knowledge,
            db,
            tenant,
            scanned_pdf(MENU_JPEG, MENU_JPEG, MENU_JPEG),
            llm=llm,
            storage=storage,
            queue=queue,
            knowledge_ocr_max_pages=1,
        )

        assert outcome.succeeded
        assert len(llm.document_calls) == 1
        ocr = (_source(db, queue.last_source_id).report or {})["ocr"]
        assert ocr["sin_leer_por_limite"] == [2, 3]

    async def test_a_model_error_on_one_page_does_not_lose_the_others(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        llm.document_responses = [LLMError("timeout"), {"legible": True, "texto": MENU_TEXT}]

        outcome = await _ingest(
            knowledge,
            db,
            tenant,
            scanned_pdf(MENU_JPEG, MENU_JPEG),
            llm=llm,
            storage=storage,
            queue=queue,
        )

        assert outcome.succeeded
        ocr = (_source(db, queue.last_source_id).report or {})["ocr"]
        assert ocr["ilegibles"] == [1] and ocr["leidas"] == [2]

    async def test_nothing_readable_fails_with_the_report(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
    ) -> None:
        llm.document_responses = [{"legible": False, "texto": ""}]

        outcome = await _ingest(
            knowledge,
            db,
            tenant,
            scanned_pdf(BLURRED_JPEG),
            llm=llm,
            storage=storage,
            queue=queue,
        )

        assert outcome.status == "failed" and outcome.error_msg == SCANNED_ILLEGIBLE
        source = _source(db, queue.last_source_id)
        assert (source.report or {})["ocr"]["ilegibles"] == [1]
        assert llm.embed_calls == []

    @pytest.mark.parametrize("vision_off", [False, True])
    async def test_without_a_reader_a_scan_fails_clearly(
        self,
        knowledge: AsyncClient,
        db: Session,
        tenant: uuid.UUID,
        llm: MockLLMProvider,
        storage: MockStorageProvider,
        queue: RecordingQueue,
        vision_off: bool,
    ) -> None:
        provider: Any = llm if vision_off else _CannotRead()
        overrides = {"vision_enabled": False} if vision_off else {}

        outcome = await _ingest(
            knowledge,
            db,
            tenant,
            scanned_pdf(MENU_JPEG),
            llm=provider,
            storage=storage,
            queue=queue,
            **overrides,
        )

        assert outcome.status == "failed" and outcome.error_msg == SCANNED_WITHOUT_OCR
        assert llm.document_calls == []
        assert (_source(db, queue.last_source_id).report or {})["ocr"]["sin_leer"] == [1]


@pytest.mark.evals
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="needs the real vision model")
class TestWithTheRealModel:
    async def test_the_fixture_menu_is_transcribed(self) -> None:
        from app.providers.llm.factory import platform_provider
        from app.services.ocr import read_scanned_pages

        settings = get_settings().model_copy(update={"llm_profile": "openai"})
        data = scanned_pdf(MENU_JPEG, BLURRED_JPEG)
        provider: Any = platform_provider(settings)

        outcome = await read_scanned_pages(
            data, ["", ""], pages=PypdfTextExtractor(), reader=provider, settings=settings
        )

        text = outcome.texts[0]
        for expected in ("Doña Rosa", "7:00", "Surco", "S/ 6", "48 horas", "Yape"):
            assert expected in text, (expected, text)
        assert outcome.read == [1] and outcome.illegible == [2]
        print(f"\nOCR real: {outcome.input_tokens} tokens in, {outcome.output_tokens} out")
