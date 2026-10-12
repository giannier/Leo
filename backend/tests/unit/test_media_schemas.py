"""The upload rules for the media library (Feature 004, TASK-405)."""

from __future__ import annotations

import pytest

from app.models.media import MEDIA_TYPES
from app.schemas.media import MB, MEDIA_LIMITS, validate_clave, validate_upload


def test_every_media_type_has_limits_and_only_those() -> None:
    assert set(MEDIA_LIMITS) == set(MEDIA_TYPES)


class TestClave:
    @pytest.mark.parametrize("clave", ["ab", "saludo", "metodos_pago", "talla_2", "a" * 40])
    def test_valid_keys(self, clave: str) -> None:
        assert validate_clave(clave) == clave

    @pytest.mark.parametrize("clave", ["", "a", "a" * 41, "Saludo", "con espacio", "ñandú", "a-b"])
    def test_invalid_keys(self, clave: str) -> None:
        with pytest.raises(ValueError, match="clave"):
            validate_clave(clave)

    def test_a_trailing_newline_is_not_accepted(self) -> None:
        with pytest.raises(ValueError, match="clave"):
            validate_clave("saludo\n")


class TestUpload:
    @pytest.mark.parametrize(
        ("tipo", "mime", "size"),
        [
            ("imagen", "image/jpeg", 5 * MB),
            ("imagen", "image/png", 1),
            ("video", "video/mp4", 16 * MB),
            ("audio", "audio/ogg", 16 * MB),
            ("documento", "application/pdf", 100 * MB),
        ],
    )
    def test_files_within_the_limits(self, tipo: str, mime: str, size: int) -> None:
        validate_upload(tipo, mime, size)

    @pytest.mark.parametrize(
        ("tipo", "mime", "size"),
        [
            ("imagen", "image/jpeg", 5 * MB + 1),
            ("video", "video/mp4", 16 * MB + 1),
            ("audio", "audio/mpeg", 16 * MB + 1),
            ("documento", "application/pdf", 100 * MB + 1),
        ],
    )
    def test_files_over_the_limit(self, tipo: str, mime: str, size: int) -> None:
        with pytest.raises(ValueError, match="larger"):
            validate_upload(tipo, mime, size)

    @pytest.mark.parametrize(
        ("tipo", "mime"),
        [
            ("imagen", "image/gif"),
            ("imagen", "application/pdf"),
            ("video", "video/quicktime"),
            ("documento", "application/msword"),
            ("audio", "video/mp4"),
        ],
    )
    def test_a_type_that_does_not_match_the_file(self, tipo: str, mime: str) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            validate_upload(tipo, mime, 1000)

    def test_an_empty_file(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_upload("imagen", "image/png", 0)

    def test_an_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="unknown media type"):
            validate_upload("sticker", "image/webp", 1000)
