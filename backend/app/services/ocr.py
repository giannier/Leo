"""Reading scanned PDF pages with the vision model (Feature 006, TASK-602).

A page whose text layer is empty (or almost: a page number) is a picture of
text. Each such page goes to the model on its own, as a one-page PDF:

* a page the model cannot read is reported by its number, and the other pages
  are still indexed (HU-01: "páginas ilegibles se reportan");
* at most `knowledge_ocr_max_pages` pages per document are read, a few at a
  time; the rest are reported as not read. That cap bounds what one upload can
  spend on the model, so it applies with billing off too;
* the model transcribes, it does not summarise: what it returns is indexed and
  counted against the plan's characters like any other text (RF-002).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.providers.llm.base import DocumentReader, LLMError
from app.providers.pdf.base import PdfPages

logger = logging.getLogger(__name__)

INSTRUCTION = (
    "Esta es una página escaneada del documento de un negocio. Transcribe todo su texto, en el "
    "orden en que se lee, conservando títulos, listas, precios y horarios tal como aparecen. No "
    "resumas, no corrijas y no agregues nada. Si la página está en blanco o no se puede leer "
    "(borrosa, cortada, ilegible), responde legible=false y texto vacío."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "legible": {"type": "boolean"},
        "texto": {"type": "string"},
    },
    "required": ["legible", "texto"],
    "additionalProperties": False,
}


@dataclass
class OcrOutcome:
    """Every page's text after OCR, and what could not be read (1-based page numbers)."""

    texts: list[str]
    read: list[int] = field(default_factory=list)
    illegible: list[int] = field(default_factory=list)
    not_read: list[int] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def report(self) -> dict[str, Any]:
        """What the panel shows next to the document."""
        return {
            "ocr": {
                "paginas": len(self.texts),
                "leidas": self.read,
                "ilegibles": self.illegible,
                "sin_leer_por_limite": self.not_read,
                "tokens_entrada": self.input_tokens,
                "tokens_salida": self.output_tokens,
            }
        }


def pages_to_read(texts: list[str], settings: Settings) -> list[int]:
    """0-based indexes of the pages without a usable text layer."""
    return [
        i for i, text in enumerate(texts) if len(text.strip()) < settings.knowledge_ocr_min_chars
    ]


async def read_scanned_pages(
    data: bytes,
    texts: list[str],
    *,
    pages: PdfPages,
    reader: DocumentReader,
    settings: Settings,
) -> OcrOutcome:
    """OCR the pages of `data` that have no text layer; keep the others as they are."""
    outcome = OcrOutcome(texts=list(texts))
    wanted = pages_to_read(texts, settings)
    limit = max(settings.knowledge_ocr_max_pages, 0)
    chosen, outcome.not_read = wanted[:limit], [i + 1 for i in wanted[limit:]]
    gate = asyncio.Semaphore(max(settings.knowledge_ocr_concurrency, 1))

    async def one(index: int) -> tuple[int, str | None, int, int]:
        async with gate:
            try:
                page = pages.page_pdf(data, index)
                reading = await reader.read_document(
                    page,
                    instruction=INSTRUCTION,
                    schema=SCHEMA,
                    timeout_seconds=settings.knowledge_ocr_timeout_seconds,
                    max_tokens=settings.knowledge_ocr_max_tokens,
                )
            except (LLMError, TimeoutError) as exc:
                logger.warning("OCR of page %d failed: %s", index + 1, exc)
                return index, None, 0, 0
            except Exception:  # a malformed page must not lose the others
                logger.exception("OCR of page %d failed unexpectedly", index + 1)
                return index, None, 0, 0
        text = str(reading.data.get("texto") or "").strip()
        legible = bool(reading.data.get("legible")) and bool(text)
        return index, text if legible else None, reading.input_tokens, reading.output_tokens

    for index, text, tokens_in, tokens_out in await asyncio.gather(*(one(i) for i in chosen)):
        outcome.input_tokens += tokens_in
        outcome.output_tokens += tokens_out
        if text is None:
            outcome.illegible.append(index + 1)
        else:
            outcome.texts[index] = text
            outcome.read.append(index + 1)
    outcome.read.sort()
    outcome.illegible.sort()
    return outcome
