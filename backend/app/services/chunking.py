"""Splitting a document into retrievable chunks.

Pure functions, no I/O: chunking is the part of the RAG pipeline most likely
to be tuned, and it is only cheap to tune if it can be unit-tested without a
database or a model.

**Why characters and not tokens.** The obvious alternative is `tiktoken`,
counting tokens the way OpenAI does. Two reasons against it: the embedding
model was chosen per profile (it was NVIDIA's when this was written), so a
tokenizer would be tied to one vendor; and it is a second dependency bought
for an approximation. Characters are exact,
free, and the budget in the `plans` table (`max_rag_chars`) is denominated in
them too, so one unit runs through the whole feature.

**Why the sizes are safe.** The embedding model accepts 512 tokens. Spanish
runs about 3.5-4 characters per token, so the 1000-character target is
roughly 260-290 tokens and the worst case (target plus overlap, 1150) is
still around 330. Comfortably inside the limit, with room for a language that
tokenizes worse than expected - silently truncating on the provider side
would drop the tail of a chunk and nobody would notice.

**Why structure first.** Splitting purely by length cuts sentences in half,
and half a sentence embeds to something that means neither half. So the text
is broken on the boundaries the author already wrote - blank lines, then
sentence ends - and only falls back to a hard cut when a single sentence is
longer than a whole chunk.

**Why overlap.** An answer that straddles a boundary is otherwise
unretrievable: neither chunk contains it whole. Repeating the tail of the
previous chunk costs a little storage and makes that case work.
"""

from __future__ import annotations

import hashlib
import re

# Target size of one chunk, in characters.
DEFAULT_CHUNK_CHARS = 1000
# How much of the previous chunk is repeated at the start of the next.
DEFAULT_OVERLAP_CHARS = 150

# Separator between the units packed into one chunk, and between the overlap
# and the chunk it prefixes.
JOIN = "\n\n"

# Sentence end: closing punctuation (optionally followed by a quote or
# bracket) then whitespace. Spanish opening marks (¿ ¡) need no special case
# because they start a sentence rather than end one.
_SENTENCE_END = re.compile(r'(?<=[.!?\u2026])["\'\u201d\u2019)\]]*\s+')

# Two or more newlines, possibly with blank space between them: a paragraph.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")

# Horizontal whitespace, including the non-breaking and figure spaces PDF
# extraction emits: on its own, and hugging a newline on either side.
_HORIZONTAL = "[ \\t\u00a0\u2007\u202f]"
_HORIZONTAL_RUN = re.compile(f"{_HORIZONTAL}+")
_SPACE_AROUND_NEWLINE = re.compile(f"{_HORIZONTAL}*\n{_HORIZONTAL}*")


def normalise(text: str) -> str:
    """Collapse the whitespace noise that PDF extraction leaves behind.

    Extracted PDFs are full of trailing spaces, doubled blank lines and
    non-breaking spaces. Left alone they inflate `char_count` - which is
    billed against the tenant's plan - and they push real content out of a
    chunk.
    """
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _HORIZONTAL_RUN.sub(" ", cleaned)
    cleaned = _SPACE_AROUND_NEWLINE.sub("\n", cleaned)
    cleaned = _PARAGRAPH_BREAK.sub(JOIN, cleaned)
    return cleaned.strip()


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split `text` into chunks of at most `max_chars + overlap` characters.

    Returns `[]` for text that is empty or only whitespace: an empty list is
    what tells the caller there is nothing to index, rather than one chunk of
    nothing.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0:
        raise ValueError("overlap cannot be negative")
    if overlap >= max_chars:
        # Otherwise every chunk would be mostly a copy of its predecessor.
        raise ValueError("overlap must be smaller than max_chars")

    cleaned = normalise(text)
    if not cleaned:
        return []

    units = _split_into_units(cleaned, max_chars)
    packed = _pack(units, max_chars)
    if overlap == 0:
        return packed
    return _apply_overlap(packed, overlap)


def _split_into_units(text: str, max_chars: int) -> list[str]:
    """Break text into pieces that each fit in a chunk.

    Paragraph first, sentence second, hard cut last. Every returned unit is
    non-empty and no longer than `max_chars`.
    """
    units: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        block = paragraph.strip()
        if not block:
            continue
        if len(block) <= max_chars:
            units.append(block)
            continue
        for sentence in _SENTENCE_END.split(block):
            piece = sentence.strip()
            if not piece:
                continue
            if len(piece) <= max_chars:
                units.append(piece)
            else:
                units.extend(_hard_split(piece, max_chars))
    return units


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last resort: cut a single over-long sentence.

    Cuts at the last space in the final fifth of the window when there is
    one, so a word is not sliced in half; a run with no spaces at all (a URL,
    a base64 blob) is cut at the limit.
    """
    pieces: list[str] = []
    remaining = text
    breakpoint_floor = max_chars - max_chars // 5
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = window.rfind(" ")
        if cut < breakpoint_floor:
            cut = max_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def _pack(units: list[str], max_chars: int) -> list[str]:
    """Greedily fill chunks with whole units."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        addition = len(unit) if not current else len(unit) + len(JOIN)
        if current and current_len + addition > max_chars:
            chunks.append(JOIN.join(current))
            current, current_len = [unit], len(unit)
            continue
        current.append(unit)
        current_len += addition

    if current:
        chunks.append(JOIN.join(current))
    return chunks


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """Prefix each chunk with the tail of the one before it.

    The tail budget includes the separator, so a chunk never grows past
    `max_chars + overlap`. It is then trimmed forward to the next word
    boundary, so the overlap never opens with a fragment of a word.
    """
    budget = overlap - len(JOIN)
    if len(chunks) < 2 or budget <= 0:
        return chunks

    result = [chunks[0]]
    for previous, chunk in zip(chunks[:-1], chunks[1:], strict=True):
        tail = previous[-budget:]
        space = tail.find(" ")
        if space != -1:
            tail = tail[space + 1 :]
        tail = tail.strip()
        result.append(f"{tail}{JOIN}{chunk}" if tail else chunk)
    return result


def content_hash(text: str) -> str:
    """SHA-256 of a chunk's text with case and whitespace ignored (Feature 006, RF-003).

    Two chunks that differ only in spacing or capitals are the same passage to a
    reader and nearly the same vector to the model: embedding both spends money
    and shows the agent the same sentence twice.
    """
    normalised = " ".join(text.split()).casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def dedupe(chunks: list[str]) -> list[str]:
    """`chunks` without the near-identical repeats, first occurrence kept, order kept."""
    seen: set[str] = set()
    kept: list[str] = []
    for chunk in chunks:
        digest = content_hash(chunk)
        if digest not in seen:
            seen.add(digest)
            kept.append(chunk)
    return kept
