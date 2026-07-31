"""Parsing catalogues a stranger uploaded (TASK-011).

The Verification asks for valid, partially invalid and **malicious** files.
Most of these cases are the third kind, because the happy path is one line and
the ways a real spreadsheet is wrong are not.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest

from app.services.csv_import import (
    MAX_ROWS,
    ImportError_,
    decode,
    neutralise_formula,
    parse_catalogue,
    parse_price,
    sniff_delimiter,
)

HEADER = "nombre,descripcion,precio,stock,categoria\n"


def _csv(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows)).encode("utf-8")


class TestFormulaInjection:
    """A cell starting `=`, `+`, `-` or `@` is executed by Excel when the file
    is opened. `=cmd|'/c calc'!A1` in a product name is working code execution
    against whoever opens the export."""

    @pytest.mark.parametrize(
        "payload",
        [
            "=cmd|'/c calc'!A1",
            "=1+1",
            "@SUM(A1:A9)",
            "+15",
            "-2+3",
            '\t=HYPERLINK("http://evil")',
        ],
    )
    def test_dangerous_prefixes_are_neutralised(self, payload: str) -> None:
        assert neutralise_formula(payload).startswith("'")

    def test_ordinary_names_are_untouched(self) -> None:
        for safe in ("Lomo saltado", "Inca Kola 500ml", "Combo 2x1"):
            assert neutralise_formula(safe) == safe

    def test_a_formula_in_a_file_is_imported_as_text(self) -> None:
        """Neutralised rather than rejected: `-15% descuento` is a real name,
        and refusing it would make the importer feel broken."""
        parsed = parse_catalogue(_csv("=1+1,desc,10.00,,Bebidas"))
        assert parsed.valid_rows == 1
        assert parsed.valid[0]["name"] == "'=1+1"

    def test_a_formula_in_the_description_is_neutralised_too(self) -> None:
        parsed = parse_catalogue(_csv("Producto,=cmd|calc,10.00,,"))
        assert str(parsed.valid[0]["description"]).startswith("'")


class TestEncoding:
    """Excel declares nothing. Guessing badly turns Cafe into mojibake."""

    def test_a_utf8_bom_is_honoured(self) -> None:
        raw = b"\xef\xbb\xbf" + HEADER.encode() + b"Caf\xc3\xa9,,10.00,,"
        assert "Café" in decode(raw)

    def test_cp1252_from_spanish_excel(self) -> None:
        """Bytes that are not valid UTF-8 but are ordinary Windows text."""
        raw = HEADER.encode() + "Café con leche,,10.00,,".encode("cp1252")
        assert "Café con leche" in decode(raw)

    def test_the_bom_does_not_break_the_first_header(self) -> None:
        raw = b"\xef\xbb\xbf" + HEADER.encode() + b"Producto,,10.00,,"
        parsed = parse_catalogue(raw)
        assert parsed.valid_rows == 1

    def test_undecodable_bytes_never_raise(self) -> None:
        """A hard failure on a 500-row file is worse than one mangled accent."""
        assert decode(bytes(range(256))) is not None


class TestDelimiter:
    """Excel in a Spanish locale writes semicolons, because the comma is the
    decimal separator."""

    def test_semicolons_are_detected(self) -> None:
        raw = b"nombre;descripcion;precio;stock;categoria\nLomo;rico;32,50;4;Platos"
        parsed = parse_catalogue(raw)
        assert parsed.valid_rows == 1
        assert parsed.valid[0]["price"] == "32.50"

    def test_tabs_are_detected(self) -> None:
        raw = b"nombre\tprecio\nLomo\t32.50"
        assert parse_catalogue(raw).valid_rows == 1

    def test_a_single_column_file_does_not_crash(self) -> None:
        """`csv.Sniffer` raises on this; counting does not."""
        assert sniff_delimiter("nombre") == ","


class TestPrices:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("19.99", "19.99"),
            ("19,99", "19.99"),
            ("1.234,56", "1234.56"),
            ("1,234.56", "1234.56"),
            ("S/ 32.00", "32.00"),
            ("  10  ", "10"),
        ],
    )
    def test_human_written_prices(self, raw: str, expected: str) -> None:
        """Whichever separator appears last is the decimal one - a thousands
        separator can never be final."""
        assert parse_price(raw) == Decimal(expected)

    def test_the_result_is_decimal_not_float(self) -> None:
        assert isinstance(parse_price("19.99"), Decimal)

    @pytest.mark.parametrize("raw", ["", "gratis", "-5.00", "S/", "abc"])
    def test_unusable_prices_raise(self, raw: str) -> None:
        """`InvalidOperation` specifically, not any exception: a blind assert
        would also pass if this raised AttributeError from a typo."""
        with pytest.raises(InvalidOperation):
            parse_price(raw)


class TestPartialSuccess:
    """plan.md: partial with an error report, never all-or-nothing."""

    def test_good_rows_survive_bad_ones(self) -> None:
        parsed = parse_catalogue(
            _csv(
                "Lomo saltado,rico,32.00,4,Platos",
                "Sin precio,,,,Bebidas",
                "Inca Kola,,5.50,20,Bebidas",
            )
        )
        assert parsed.valid_rows == 2
        assert [row.row for row in parsed.invalid] == [3]
        assert parsed.invalid[0].reason == "precio_invalido"

    def test_row_numbers_match_the_customers_editor(self) -> None:
        """Header is row 1, so the first data row is row 2."""
        parsed = parse_catalogue(_csv("Malo,,no-es-precio,,"))
        assert parsed.invalid[0].row == 2

    def test_blank_lines_are_skipped_not_reported(self) -> None:
        parsed = parse_catalogue(_csv("Lomo,,32.00,,", "", "  ,  ,  ,  ,  "))
        assert parsed.valid_rows == 1
        assert parsed.invalid == []

    @pytest.mark.parametrize(
        ("row", "reason"),
        [
            (",,10.00,,", "nombre_vacio"),
            ("Producto,,10.00,-4,", "stock_negativo"),
            ("Producto,,10.00,muchos,", "stock_invalido"),
        ],
    )
    def test_each_failure_names_itself(self, row: str, reason: str) -> None:
        parsed = parse_catalogue(_csv(row))
        assert parsed.invalid[0].reason == reason


class TestHeaders:
    def test_spanish_headers_are_understood(self) -> None:
        raw = b"producto,detalle,precio,cantidad,rubro\nLomo,rico,32.00,4,Platos"
        assert parse_catalogue(raw).valid_rows == 1

    def test_english_headers_are_understood(self) -> None:
        raw = b"name,description,price,stock,category\nSteak,nice,32.00,4,Mains"
        assert parse_catalogue(raw).valid_rows == 1

    def test_a_missing_required_column_is_a_whole_file_failure(self) -> None:
        """Not a per-row error: nothing in the file can be salvaged."""
        with pytest.raises(ImportError_, match="missing_columns"):
            parse_catalogue(b"nombre,categoria\nLomo,Platos")

    def test_an_empty_file_is_rejected(self) -> None:
        with pytest.raises(ImportError_, match="empty_file"):
            parse_catalogue(b"")


class TestResourceLimits:
    def test_an_oversized_file_is_refused_before_parsing(self) -> None:
        with pytest.raises(ImportError_, match="file_too_large"):
            parse_catalogue(b"x" * (5 * 1024 * 1024 + 1))

    def test_row_count_is_capped(self) -> None:
        rows = ["Producto,,10.00,," for _ in range(MAX_ROWS + 50)]
        parsed = parse_catalogue(_csv(*rows))
        assert parsed.valid_rows <= MAX_ROWS
        assert parsed.invalid[-1].reason == "too_many_rows"

    def test_an_enormous_field_is_rejected_not_truncated(self) -> None:
        parsed = parse_catalogue(_csv(f"{'x' * 5000},,10.00,,"))
        assert parsed.invalid[0].reason == "nombre_demasiado_largo"
