"""The payment receipt as a one-page PDF (Feature 009, HU-05).

A simple receipt with an internal correlative (LEO-000001...), not a SUNAT
invoice: electronic invoicing is explicitly post-MVP. Written by hand rather than
with a PDF library (Giannier, 2026-10-14: no new dependency for this): one page,
Helvetica in WinAnsi so Spanish accents print, text only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
LEFT = 64


@dataclass(frozen=True)
class ReceiptData:
    number: str
    issued_at: datetime
    business: str
    concept: str
    reference: str
    amount_usd: str
    paid_local: str | None
    method: str


def _escape(value: str) -> bytes:
    # WinAnsi covers Spanish; anything outside it prints as "?" rather than breaking the file.
    raw = value.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _line(x: int, y: int, size: int, text: str, *, bold: bool = False) -> bytes:
    font = b"/F2" if bold else b"/F1"
    return b"BT " + font + f" {size} Tf {x} {y} Td (".encode() + _escape(text) + b") Tj ET\n"


def render_receipt(data: ReceiptData) -> bytes:
    y = PAGE_HEIGHT - 90
    rows = [
        ("Comprobante de pago", 20, True),
        (f"N.° {data.number}", 12, True),
        ("", 10, False),
        (f"Fecha: {data.issued_at.strftime('%d/%m/%Y %H:%M')} (UTC)", 11, False),
        (f"Negocio: {data.business}", 11, False),
        (f"Concepto: {data.concept}", 11, False),
        (f"Referencia: {data.reference}", 11, False),
        (f"Medio de pago: {data.method}", 11, False),
        ("", 10, False),
        (f"Total: {data.amount_usd}", 14, True),
    ]
    if data.paid_local:
        rows.append((f"Pagado en moneda local: {data.paid_local}", 11, False))
    rows += [
        ("", 10, False),
        ("Leo, de Zentek360. Este comprobante no es una factura electrónica.", 9, False),
    ]
    stream = b""
    for text, size, bold in rows:
        if text:
            stream += _line(LEFT, y, size, text, bold=bold)
        y -= size + 12

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
        "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>".encode(),
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
