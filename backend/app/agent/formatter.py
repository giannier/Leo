"""From the model's text to the WhatsApp bubbles (Feature 004, TASK-416).

A port of the *Formato WhatsApp1* node of Capacita v21
(`referencias/capacita_v21/fw21.js`), which runs in production and is covered by
the seven cases of `harness21.js`. No LLM is involved: it is free, instant and
always gives the same answer for the same text, which matters because since
2026-10-01 every text message is billed (C-11).

Three steps:

1. Drop what the customer must never see: internal notes the model sometimes
   writes ("no responder", "nota interna"), code blocks and loose JSON.
2. Rewrite Markdown as WhatsApp markup (`**x**` to `*x*`, `- item` to a bullet,
   `[text](url)` to `text: url`).
3. Split in two only when the text is long, only between paragraphs and as close
   to the middle as possible, so a sentence is never cut. How many bubbles are
   allowed depends on the detail level and on what the tools already sent
   (`TurnBudget.remaining_text_bubbles`).

`agent/core.py` keeps its own `whatsapp_text` and `usable_answer` for the
single-reply path that exists today; the pipeline (TASK-421) replaces that path
with this module.
"""

from __future__ import annotations

import re
from typing import Literal

from app.agent.budget import TurnBudget

Detail = Literal["breve", "moderado", "extenso"]

# From this length a text may be split in two (`burbuja_max_caracteres` in v21).
SPLIT_THRESHOLD = 700

# The most bubbles each detail level may use. A brief answer is never split.
_MAX_BUBBLES: dict[str, int] = {"breve": 1, "moderado": 2, "extenso": 2}
_DEFAULT_DETAIL: Detail = "moderado"

_INTERNAL_NOTE = re.compile(
    r"^\s*(no responder|no es interacci[oó]n|mensaje vac[ií]o|mensaje no es consulta"
    r"|nota interna|\[interno\])",
    re.IGNORECASE,
)
_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_LOOSE_JSON = re.compile(r'^\s*\{\s*"[\s\S]*?\}\s*$', re.MULTILINE)

# `(?=\S)` / `(?<=\S)` keep the delimiters glued to real text, so arithmetic like
# `2 ** 3` and a row of `***` separators are left alone.
_BOLD_ITALIC = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.DOTALL)
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_BOLD_UNDERSCORE = re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.DOTALL)
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
# WhatsApp has no headings: the line becomes bold.
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+)$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_BULLET = re.compile(r"^[ \t]*[-+*][ \t]+(?=\S)", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_INLINE_CODE = re.compile(r"`{1,3}([^`]+)`{1,3}")
_TRAILING_BLANKS = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_LINES = re.compile(r"\n{3,}")
_PARAGRAPH_BREAK = re.compile(r"\n\n+")


def strip_internal(text: str) -> str:
    """Remove internal notes, code blocks and loose JSON the model wrote."""
    text = _CODE_BLOCK.sub("", text)
    text = _LOOSE_JSON.sub("", text)
    return "\n".join(line for line in text.split("\n") if not _INTERNAL_NOTE.match(line))


def to_whatsapp_markup(text: str) -> str:
    """Rewrite Markdown as the markup WhatsApp renders, and tidy the spacing."""
    if not text.strip():
        return ""
    text = _BOLD_ITALIC.sub(r"*\1*", text)
    text = _BOLD.sub(r"*\1*", text)
    text = _BOLD_UNDERSCORE.sub(r"*\1*", text)
    text = _STRIKE.sub(r"~\1~", text)
    text = _HEADING.sub(r"*\1*", text)
    text = _LINK.sub(r"\1: \2", text)
    text = _BULLET.sub("• ", text)
    text = _QUOTE.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _TRAILING_BLANKS.sub("", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _split_in_two(text: str) -> list[str]:
    """Cut at the paragraph break closest to the middle; one part if there is none."""
    paragraphs = _PARAGRAPH_BREAK.split(text)
    if len(paragraphs) < 2:
        return [text]
    cut, best, accumulated = 1, float("inf"), 0
    for index in range(len(paragraphs) - 1):
        accumulated += len(paragraphs[index]) + 2
        distance = abs(accumulated - len(text) / 2)
        if distance < best:
            best, cut = distance, index + 1
    return ["\n\n".join(paragraphs[:cut]), "\n\n".join(paragraphs[cut:])]


def to_bubbles(
    texto: str,
    detalle: str,
    budget: TurnBudget,
    *,
    threshold: int = SPLIT_THRESHOLD,
) -> list[str]:
    """The WhatsApp messages for the model's final text.

    How many there can be:

    | detail   | bubbles                                             |
    |----------|-----------------------------------------------------|
    | breve    | 1                                                   |
    | moderado | 1, or 2 if the text is longer than `threshold`     |
    | extenso  | 1, or 2 if the text is longer than `threshold`     |

    and never more than `budget.remaining_text_bubbles()`: once the tools have
    sent two things the text goes out in one bubble however long it is (C-11).
    Empty text gives an empty list, so nothing is sent.
    """
    text = to_whatsapp_markup(strip_internal(texto or ""))
    if not text:
        return []

    allowed = min(
        _MAX_BUBBLES.get(detalle, _MAX_BUBBLES[_DEFAULT_DETAIL]),
        budget.remaining_text_bubbles(),
    )
    if allowed < 1:
        return []

    parts = [text]
    if allowed >= 2 and len(text) > threshold:
        parts = _split_in_two(text)
    return [part.strip() for part in parts if part.strip()]
