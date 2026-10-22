"""Reading a chat exported from WhatsApp (017, phase 2)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest

from app.learning.whatsapp_export import (
    ExportError,
    conversations,
    parse_chat,
    participants,
    read_upload,
    to_turns,
)

ANDROID = """\
12/10/2025 14:30 - Los mensajes y llamadas están cifrados de extremo a extremo.
12/10/2025 14:32 - Ana: Hola, buenas tardes
12/10/2025 14:33 - Tienda Sol: Hola Ana, ¿en qué te ayudo?
12/10/2025 14:34 - Ana: Quiero saber el precio
del polo básico
y si tienen talla M
12/10/2025 14:35 - Tienda Sol: PTT-20251012-WA0001.opus (archivo adjunto)
12/10/2025 14:36 - Ana: <Multimedia omitido>
12/10/2025 14:37 - Tienda Sol: El polo cuesta S/ 35: sí hay talla M
13/10/2025 09:00 - Ana: Gracias
"""

IOS = """\
‎[12/10/25, 2:32:45 p. m.] Ana: Hola
[12/10/25, 2:33:10 p. m.] Tienda Sol: ‎<adjunto: 00000012-AUDIO-2025-10-12-14-33-10.opus>
[12/10/25, 2:34:00 p. m.] Tienda Sol: Te escucho: ¿qué necesitas?
‎[12/10/25, 12:05:00 a. m.] Ana: ‎<adjunto: 00000013-PHOTO-2025-10-12-00-05-00.jpg>
"""


class TestParsing:
    def test_android_lines_multiline_messages_and_system_lines(self) -> None:
        messages = parse_chat(ANDROID)

        assert [(m.sender, m.kind) for m in messages] == [
            ("Ana", "texto"), ("Tienda Sol", "texto"), ("Ana", "texto"), ("Tienda Sol", "audio"),
            ("Ana", "omitido"), ("Tienda Sol", "texto"), ("Ana", "texto"),
        ]  # fmt: skip
        assert messages[2].text == "Quiero saber el precio\ndel polo básico\ny si tienen talla M"
        assert messages[3].attachment == "PTT-20251012-WA0001.opus" and messages[3].text == ""
        assert messages[5].text == "El polo cuesta S/ 35: sí hay talla M"
        assert messages[0].when == datetime(2025, 10, 12, 14, 32)

    def test_ios_lines_twelve_hour_clock_and_attachments(self) -> None:
        messages = parse_chat(IOS)

        assert [(m.sender, m.kind) for m in messages] == [
            ("Ana", "texto"), ("Tienda Sol", "audio"), ("Tienda Sol", "texto"), ("Ana", "imagen"),
        ]  # fmt: skip
        assert messages[0].when == datetime(2025, 10, 12, 14, 32, 45)
        assert messages[3].when == datetime(2025, 10, 12, 0, 5, 0)  # 12:05 a. m.
        assert messages[1].attachment == "00000012-AUDIO-2025-10-12-14-33-10.opus"
        assert messages[2].text == "Te escucho: ¿qué necesitas?"

    def test_a_text_that_is_not_a_chat_has_no_messages(self) -> None:
        assert parse_chat("hola\nesto no es un chat\n") == []

    def test_who_wrote_and_how_much(self) -> None:
        assert participants(parse_chat(ANDROID)) == [("Ana", 4), ("Tienda Sol", 3)]

    def test_a_long_silence_starts_another_conversation(self) -> None:
        groups = conversations(parse_chat(ANDROID))

        assert [len(g) for g in groups] == [6, 1]

    def test_names_become_roles(self) -> None:
        turns = to_turns(parse_chat(ANDROID), ["Tienda Sol"])

        assert [t.de for t in turns] == [
            "cliente", "vendedor", "cliente", "vendedor", "cliente", "vendedor", "cliente",
        ]  # fmt: skip
        # The name of the sender is gone; a name inside a message is for the anonymiser.
        assert all("Tienda Sol" not in t.texto for t in turns)


def zipped(**files: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name.replace("__", "/"), content)
    return buffer.getvalue()


class TestUploads:
    def test_a_plain_text_file(self) -> None:
        upload = read_upload(ANDROID.encode())

        assert len(upload.messages) == 7 and upload.audio_names == []

    def test_a_zip_with_the_chat_and_its_voice_notes(self) -> None:
        data = zipped(
            **{
                "Chat de WhatsApp con Ana.txt": ANDROID.encode(),
                "PTT-20251012-WA0001.opus": b"OggS-voice",
                "IMG-1.jpg": b"\xff\xd8\xff",
            }
        )

        upload = read_upload(data)

        assert upload.audio_names == ["PTT-20251012-WA0001.opus"]
        assert upload.read_audio("PTT-20251012-WA0001.opus") == b"OggS-voice"
        assert upload.read_audio("nope.opus") is None

    def test_utf16_exports_are_read(self) -> None:
        assert len(read_upload(ANDROID.encode("utf-16")).messages) == 7

    @pytest.mark.parametrize(
        ("data", "code"),
        [
            (b"", "empty_file"),
            (b"hola, esto no es un chat", "no_messages"),
            (b"PK\x03\x04 not really a zip", "bad_zip"),
            (zipped(**{"foto.jpg": b"x"}), "no_chat"),
        ],
    )
    def test_what_is_not_a_chat_is_refused_with_a_reason(self, data: bytes, code: str) -> None:
        with pytest.raises(ExportError) as caught:
            read_upload(data)

        assert caught.value.code == code

    def test_a_path_that_climbs_out_of_the_zip_is_never_read(self) -> None:
        data = zipped(**{"chat.txt": ANDROID.encode(), "..__evil.opus": b"x", "ok.opus": b"y"})

        upload = read_upload(data)

        assert upload.read_audio("evil.opus") is None
