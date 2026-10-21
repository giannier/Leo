"""One website, asked once (feature 019)."""

from __future__ import annotations

import pytest

from app.services.website import normalize_website


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mitienda.pe", "https://mitienda.pe"),
        ("www.mitienda.pe/", "https://www.mitienda.pe/"),
        ("https://zentek360.com/", "https://zentek360.com/"),
        ("http://mitienda.pe/productos", "http://mitienda.pe/productos"),
        ("  zentek360.com  ", "https://zentek360.com"),
        ("", None),
        (None, None),
        ("mi tienda", None),
        ("@mitienda", None),
        ("instagram.com/mitienda", None),
        ("https://www.facebook.com/mitienda", None),
        ("wa.me/51999999999", None),
    ],
)
def test_what_counts_as_a_website(raw: str | None, expected: str | None) -> None:
    assert normalize_website(raw) == expected
