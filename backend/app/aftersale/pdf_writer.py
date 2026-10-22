"""A very small PDF writer for the documents Leo makes itself (feature 019, phase G).

Text only, A4, the two built-in Helvetica fonts: enough for an order summary and nothing else, so
the platform needs no PDF library for it. Spanish accents work (WinAnsi); a character outside that
set is written as «?» rather than failing the document.
"""

from __future__ import annotations

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 56
BODY_SIZE = 11
TITLE_SIZE = 18
LEADING = 16
# Helvetica averages a little over half an em per character; this is on the safe side.
CHARS_PER_LINE = 82

Line = tuple[str, bool, int]  # text, bold, size


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(text: str, width: int = CHARS_PER_LINE) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        while len(word) > width:  # a single long token (a URL) is cut, not allowed to overflow
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current or not lines:
        lines.append(current)
    return lines


class SimplePdf:
    def __init__(self) -> None:
        self._lines: list[Line] = []

    def title(self, text: str) -> None:
        self._lines.append((text, True, TITLE_SIZE))
        self.gap()

    def heading(self, text: str) -> None:
        self._lines.append((text, True, BODY_SIZE + 1))

    def text(self, text: str, *, bold: bool = False) -> None:
        for piece in _wrap(text):
            self._lines.append((piece, bold, BODY_SIZE))

    def gap(self) -> None:
        self._lines.append(("", False, BODY_SIZE))

    def render(self) -> bytes:
        pages: list[list[Line]] = [[]]
        used = 0
        usable = PAGE_HEIGHT - 2 * MARGIN
        for line in self._lines:
            height = line[2] + (LEADING - BODY_SIZE)
            if used + height > usable:
                pages.append([])
                used = 0
            pages[-1].append(line)
            used += height

        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # placeholders, filled in once the page ids are known
        tree = add(b"")
        regular = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        )
        bold = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        )
        page_ids: list[int] = []
        for page in pages:
            stream = bytearray()
            y = PAGE_HEIGHT - MARGIN
            for text, is_bold, size in page:
                y -= size + (LEADING - BODY_SIZE)
                if text:
                    font = b"/F2" if is_bold else b"/F1"
                    stream += b"BT " + font + b" %d Tf %d %d Td (" % (size, MARGIN, y)
                    stream += _escape(text) + b") Tj ET\n"
            content = add(
                b"<< /Length %d >>\nstream\n" % len(stream) + bytes(stream) + b"\nendstream"
            )
            page_ids.append(
                add(
                    b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] "
                    b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                    % (tree, PAGE_WIDTH, PAGE_HEIGHT, regular, bold, content)
                )
            )
        kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
        objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
        objects[tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for offset in offsets:
            out += b"%010d 00000 n \n" % offset
        out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1,
            catalog,
            xref,
        )
        return bytes(out)
