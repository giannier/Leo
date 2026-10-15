"""Parse a product catalogue out of a spreadsheet export (CSV or XLSX).

This code reads files a stranger uploaded, so most of it is about the ways a
real one is wrong rather than the happy path.

**Formula injection.** A cell beginning `=`, `+`, `-`, `@` or a control
character is executed as a formula by Excel and LibreOffice when the file is
later opened. `=cmd|'/c calc'!A1` in a product name is a working remote code
execution against whoever opens the export. The value is neutralised on the
way in, not on the way out: this data also reaches WhatsApp messages and the
agent's prompt, and only one of those paths is a CSV.

**Encoding.** Excel on a Spanish Windows writes cp1252 with no declaration,
and Excel on macOS writes UTF-8 with a BOM. Neither says which. Guessing is
unavoidable; guessing badly turns "Café" into "CafÃ©" and the customer sees a
catalogue full of mojibake.

**Delimiter.** Excel in a Spanish locale exports semicolons, because the comma
is the decimal separator. A parser that assumes commas reads such a file as
one enormous column and reports every row invalid, which looks like the
customer's file is broken rather than ours.

**Decimals.** `19,99` and `19.99` are both real. So is `1.234,56`.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import openpyxl

from app.services.product_tags import TagsInvalid, clean_tags, split_tags

# Bounds an upload before any of it is parsed. Generous for a real catalogue,
# small enough that a hostile file cannot exhaust memory.
MAX_ROWS = 5_000
MAX_BYTES = 5 * 1024 * 1024
MAX_FIELD_CHARS = 2_000

# Characters that make a spreadsheet treat a cell as a formula. The control
# characters matter too: Excel strips leading whitespace and then re-reads the
# first visible character, so "\t=SUM(...)" is a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

CANONICAL_COLUMNS = (
    "name",
    "description",
    "price",
    "stock",
    "category",
    # Fit for sale (Feature 004). All optional: a file without them imports as
    # fixed-price, physical, single, not highlighted products.
    "modo_venta",
    "es_digital",
    "bundle_size",
    "bundle_permite_libres",
    "destacado",
    # Feature 018: "oferta, verano" in one cell.
    "tags",
)
REQUIRED_COLUMNS = ("name", "price")

BUNDLE_MIN_SIZE = 2
BUNDLE_MAX_SIZE = 50

_TRUE_WORDS = frozenset({"si", "sí", "s", "yes", "y", "true", "verdadero", "1", "x"})
_FALSE_WORDS = frozenset({"no", "n", "false", "falso", "0"})
_SALE_MODES = {
    "precio_fijo": "precio_fijo",
    "precio fijo": "precio_fijo",
    "fijo": "precio_fijo",
    "cotizacion": "cotizacion",
    "cotización": "cotizacion",
    "cotizar": "cotizacion",
}

# Spanish headers as a customer's own spreadsheet would spell them.
_HEADER_ALIASES = {
    "nombre": "name",
    "producto": "name",
    "name": "name",
    "descripcion": "description",
    "descripción": "description",
    "detalle": "description",
    "description": "description",
    "precio": "price",
    "price": "price",
    "stock": "stock",
    "cantidad": "stock",
    "existencias": "stock",
    "categoria": "category",
    "categoría": "category",
    "category": "category",
    "rubro": "category",
    "modo_venta": "modo_venta",
    "modo de venta": "modo_venta",
    "modo": "modo_venta",
    "es_digital": "es_digital",
    "digital": "es_digital",
    "bundle_size": "bundle_size",
    "paquete": "bundle_size",
    "tamano_paquete": "bundle_size",
    "tamaño_paquete": "bundle_size",
    "bundle_permite_libres": "bundle_permite_libres",
    "permite_libres": "bundle_permite_libres",
    "libres": "bundle_permite_libres",
    "destacado": "destacado",
    "etiquetas": "tags",
    "etiqueta": "tags",
    "tags": "tags",
}

_CURRENCY_NOISE = re.compile(r"[^\d,.\-]")


class ImportError_(ValueError):
    """The file could not be read at all, so no row-level report is possible."""


@dataclass(frozen=True)
class InvalidRow:
    """One rejected row, numbered as the customer sees it in their editor."""

    row: int
    reason: str


@dataclass
class ParsedCatalogue:
    valid: list[dict[str, object]] = field(default_factory=list)
    invalid: list[InvalidRow] = field(default_factory=list)

    @property
    def valid_rows(self) -> int:
        return len(self.valid)


def decode(raw: bytes) -> str:
    """Best-effort text from an undeclared spreadsheet export.

    Ordered by how much evidence each attempt carries. A BOM is a statement,
    so it wins. Strict UTF-8 is next: it is a self-validating encoding, and a
    byte sequence that decodes cleanly as UTF-8 almost never was anything
    else. cp1252 is the fallback because it is what Excel on a Spanish
    Windows writes, and unlike latin-1 it maps the smart quotes such files
    are full of.

    latin-1 is last and cannot fail - every byte is a character - which is
    what makes it the right floor: a mangled accent is recoverable by the
    customer, a hard failure on a 500-row file is not.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")  # pragma: no cover - unreachable


def sniff_delimiter(sample: str) -> str:
    """Pick the separator by counting candidates in the header line.

    `csv.Sniffer` is the obvious choice and is not used: it raises on files it
    cannot classify, including a single-column catalogue, which is a perfectly
    legitimate upload. Counting is dumber and never refuses.
    """
    header = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {candidate: header.count(candidate) for candidate in (";", ",", "\t", "|")}
    best = max(counts, key=lambda candidate: counts[candidate])
    return best if counts[best] > 0 else ","


def neutralise_formula(value: str) -> str:
    """Disarm a cell that a spreadsheet would execute.

    Prefixed with an apostrophe rather than rejected: `-15% descuento` and
    `+Envio gratis` are ordinary product names, and refusing them would make
    the importer feel broken for text that is completely legitimate. Excel
    reads the apostrophe as "this is literal text" and does not display it.
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def parse_price(raw: str) -> Decimal:
    """Read a price written by a human in any of the usual ways.

    `1.234,56` (European), `1,234.56` (US) and `19.99` all have to work. The
    rule that separates them: whichever of `.` or `,` appears **last** is the
    decimal separator, because a thousands separator can never be the final
    one.

    Returns Decimal, never float - money is Decimal everywhere (Constitution),
    and `0.1 + 0.2` is exactly the kind of error a catalogue must not carry.
    """
    cleaned = _CURRENCY_NOISE.sub("", raw.strip())
    if not cleaned:
        raise InvalidOperation("empty")

    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")

    if last_dot > last_comma:
        cleaned = cleaned.replace(",", "")
    elif last_comma > last_dot:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    # Neither present, or only one occurrence of a single separator that we
    # cannot distinguish from a decimal point: leave it as it is.

    price = Decimal(cleaned)
    if price < 0:
        raise InvalidOperation("negative")
    return price


def parse_flag(raw: str) -> bool:
    """A yes/no cell as a customer writes it: `si`, `no`, `1`, `x`, blank.

    A blank cell is "no", because an optional column left empty must not reject
    the row. Anything else that is not a recognised word raises `ValueError`.
    """
    word = raw.strip().lower()
    if not word or word in _FALSE_WORDS:
        return False
    if word in _TRUE_WORDS:
        return True
    raise ValueError(word)


def _normalise_header(cell: str) -> str:
    return _HEADER_ALIASES.get(cell.strip().lower().lstrip("﻿"), "")


def _is_xlsx(raw: bytes) -> bool:
    """XLSX is a zip archive; CSV never starts with the local-file signature."""
    return raw[:4] == b"PK\x03\x04"


def _read_xlsx(raw: bytes) -> list[list[str]]:
    """Rows from the first worksheet, as strings.

    `read_only=True` streams the sheet instead of building the whole object
    graph, which is what keeps a wide workbook from becoming a memory problem
    - an XLSX is a zip, so five compressed megabytes can be far more once
    expanded, and the byte ceiling alone does not bound that.

    `data_only=True` returns the values Excel last calculated rather than
    formula text. That is what the customer sees on screen, and it means a
    `=A1*2` cell arrives as a number instead of as a string this parser would
    have to treat as hostile. A cell that is *literally* text beginning with
    `=` still reaches `neutralise_formula` like any other.
    """
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(raw), read_only=True, data_only=True, keep_links=False
        )
    except Exception as exc:  # openpyxl raises a wide variety on a bad file
        raise ImportError_("invalid_xlsx") from exc

    try:
        sheet = workbook.worksheets[0]
    except IndexError as exc:
        raise ImportError_("empty_file") from exc

    rows: list[list[str]] = []
    try:
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            # +1 for the header. Bounded here as well as in the row loop
            # below, because iteration itself is what a decompression bomb
            # makes expensive.
            if index > MAX_ROWS:
                break
            rows.append(
                ["" if cell is None else str(cell) for cell in row[: len(CANONICAL_COLUMNS) + 5]]
            )
    finally:
        workbook.close()

    if not rows:
        raise ImportError_("empty_file")
    return rows


def parse_catalogue(raw: bytes) -> ParsedCatalogue:
    """Turn an uploaded file into rows to create and rows to report.

    Accepts CSV and XLSX, detected by content rather than by the filename the
    browser supplied - a customer who renames `catalogo.xlsx` to `.csv` should
    still get their products, and an attacker's extension proves nothing.

    Partial success by design (plan.md): a customer with 500 products and
    three bad prices gets 497 imported and a list of three, not a rejection.
    """
    if len(raw) > MAX_BYTES:
        raise ImportError_("file_too_large")
    if not raw.strip():
        raise ImportError_("empty_file")

    if _is_xlsx(raw):
        rows = iter(_read_xlsx(raw))
    else:
        text = decode(raw)
        if not text.strip():
            raise ImportError_("empty_file")
        rows = iter(csv.reader(io.StringIO(text), delimiter=sniff_delimiter(text)))

    reader: Iterator[list[str]] = rows
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ImportError_("empty_file") from exc

    columns = [_normalise_header(cell) for cell in header]
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ImportError_(f"missing_columns:{','.join(missing)}")

    result = ParsedCatalogue()
    for offset, row in enumerate(reader):
        # +2: the header is row 1, and the customer's editor counts from 1.
        number = offset + 2
        if len(result.valid) + len(result.invalid) >= MAX_ROWS:
            result.invalid.append(InvalidRow(row=number, reason="too_many_rows"))
            break
        if not any(cell.strip() for cell in row):
            continue

        record = dict(zip(columns, row, strict=False))
        parsed = _parse_row(record, number)
        if isinstance(parsed, InvalidRow):
            result.invalid.append(parsed)
        else:
            result.valid.append(parsed)

    return result


def _parse_row(record: dict[str, str], number: int) -> dict[str, object] | InvalidRow:
    name = (record.get("name") or "").strip()
    if not name:
        return InvalidRow(row=number, reason="nombre_vacio")
    if len(name) > MAX_FIELD_CHARS:
        return InvalidRow(row=number, reason="nombre_demasiado_largo")

    try:
        price = parse_price(record.get("price") or "")
    except (InvalidOperation, ArithmeticError, ValueError):
        return InvalidRow(row=number, reason="precio_invalido")

    stock_raw = (record.get("stock") or "").strip()
    stock: int | None = None
    if stock_raw:
        try:
            stock = int(Decimal(_CURRENCY_NOISE.sub("", stock_raw).replace(",", ".")))
        except (InvalidOperation, ArithmeticError, ValueError):
            return InvalidRow(row=number, reason="stock_invalido")
        if stock < 0:
            return InvalidRow(row=number, reason="stock_negativo")

    sale_mode = _SALE_MODES.get((record.get("modo_venta") or "").strip().lower())
    if sale_mode is None:
        if (record.get("modo_venta") or "").strip():
            return InvalidRow(row=number, reason="modo_venta_invalido")
        sale_mode = "precio_fijo"

    bundle_size: int | None = None
    bundle_raw = (record.get("bundle_size") or "").strip()
    if bundle_raw:
        try:
            bundle_size = int(Decimal(bundle_raw))
        except (InvalidOperation, ArithmeticError, ValueError):
            return InvalidRow(row=number, reason="paquete_invalido")
        if not BUNDLE_MIN_SIZE <= bundle_size <= BUNDLE_MAX_SIZE:
            return InvalidRow(row=number, reason="paquete_invalido")

    flags: dict[str, bool] = {}
    for column in ("es_digital", "bundle_permite_libres", "destacado"):
        try:
            flags[column] = parse_flag(record.get(column) or "")
        except ValueError:
            return InvalidRow(row=number, reason=f"{column}_invalido")

    try:
        tags = clean_tags(neutralise_formula(t) for t in split_tags(record.get("tags") or ""))
    except TagsInvalid:
        return InvalidRow(row=number, reason="etiquetas_invalidas")

    description = (record.get("description") or "").strip()[:MAX_FIELD_CHARS]
    category = (record.get("category") or "").strip()[:MAX_FIELD_CHARS]

    return {
        "name": neutralise_formula(name),
        "description": neutralise_formula(description) or None,
        # Quantised to the two decimals the column stores, so the preview
        # shows the customer the price that will actually be saved.
        "price": str(price.quantize(Decimal("0.01"))),
        "stock": stock,
        "category": neutralise_formula(category) or None,
        "modo_venta": sale_mode,
        "bundle_size": bundle_size,
        "tags": tags,
        **flags,
    }


TEMPLATE_CSV = (
    "nombre,descripcion,precio,stock,categoria,etiquetas\r\n"
    'Lomo saltado,Plato criollo con arroz,32.00,,Platos de fondo,"criollo, almuerzo"\r\n'
    "Inca Kola 500ml,Bebida gaseosa,5.50,48,Bebidas,\r\n"
)
