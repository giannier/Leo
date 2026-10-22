"""Reading the chat a business exports from WhatsApp (feature 017, phase 2).

WhatsApp exports a chat as a text file (alone, or inside a zip with the voice notes and the
pictures). Two shapes of line exist, and both are read:

* Android: ``12/10/2025 14:32 - Ana: Hola`` (the clock may be 12 hours: ``2:32 p. m.``)
* iOS:     ``[12/10/25, 2:32:45 p. m.] Ana: Hola`` (invisible marks around the attachments)

A message may go on over several lines. A line with a time but no ``sender:`` is the system
talking (encryption notice, someone joined) and is skipped. Dates are read day first, as WhatsApp
writes them for a phone in Spanish. A voice note appears as an attachment
(``PTT-20251012-WA0001.opus (archivo adjunto)`` or ``<adjunto: 00000012-AUDIO-2025-10-12.opus>``).

Nothing here touches the disk or the database: the original lives in memory while it is read.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Kind = Literal["texto", "audio", "imagen", "otro", "omitido"]
Role = Literal["cliente", "vendedor"]

MAX_ZIP_ENTRIES = 3000
MAX_UNCOMPRESSED = 300 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
# A silence this long starts another conversation.
CONVERSATION_GAP = timedelta(hours=8)

_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"), None)
_CLOCK = r"(\d{1,2}:\d{2}(?::\d{2})?)(?:\s?([ap])\.?\s?m\.?)?"
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{2,4})"
_ANDROID = re.compile(rf"^{_DATE},?\s+{_CLOCK}\s+-\s+(.*)$", re.IGNORECASE)
_IOS = re.compile(rf"^\[{_DATE},?\s+{_CLOCK}\]\s+(.*)$", re.IGNORECASE)
_ATTACHED_IOS = re.compile(r"<(?:adjunto|attached):\s*(.+?)>", re.IGNORECASE)
_ATTACHED_ANDROID = re.compile(
    r"^(.+?\.\w{2,5})\s+\((?:archivo adjunto|file attached)\)$", re.IGNORECASE
)
_OMITTED = re.compile(r"^<(?:multimedia omitido|media omitted)>$", re.IGNORECASE)
_AUDIO_EXTENSIONS = {"opus", "ogg", "oga", "m4a", "aac", "mp3", "amr", "wav"}
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}


class ExportError(ValueError):
    """The file is not a WhatsApp export, or it is not safe to open."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    when: datetime
    kind: Kind = "texto"
    attachment: str | None = None


@dataclass(frozen=True)
class Turn:
    """One message with the personal side removed: who spoke is a role, not a name."""

    de: Role
    texto: str
    kind: Kind = "texto"


def _clean(line: str) -> str:
    return line.translate(_INVISIBLE).replace(" ", " ").replace("\xa0", " ").rstrip("\r")


def _when(day: str, month: str, year: str, clock: str, meridiem: str | None) -> datetime | None:
    try:
        number = int(year)
        full_year = number + 2000 if number < 100 else number
        parts = [int(p) for p in clock.split(":")]
        hour, minute = parts[0], parts[1]
        second = parts[2] if len(parts) == 3 else 0
        if meridiem:
            hour = hour % 12 + (12 if meridiem.lower() == "p" else 0)
        return datetime(full_year, int(month), int(day), hour, minute, second)
    except ValueError:
        return None


def _kind_of(name: str) -> Kind:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension in _AUDIO_EXTENSIONS:
        return "audio"
    if extension in _IMAGE_EXTENSIONS:
        return "imagen"
    return "otro"


def _classify(body: str) -> tuple[str, Kind, str | None]:
    """The text of a message, its kind, and the attachment name if it has one."""
    stripped = body.strip()
    if _OMITTED.match(stripped):
        return "", "omitido", None
    ios = _ATTACHED_IOS.search(stripped)
    if ios:
        name = ios.group(1).strip()
        rest = _ATTACHED_IOS.sub("", stripped).strip()
        return rest, _kind_of(name), name
    android = _ATTACHED_ANDROID.match(stripped)
    if android:
        name = android.group(1).strip()
        return "", _kind_of(name), name
    return stripped, "texto", None


def parse_chat(content: str) -> list[ChatMessage]:
    """The messages of an exported chat, oldest first; system lines left out."""
    messages: list[ChatMessage] = []
    pending: tuple[datetime, str] | None = None  # (when, "sender: text so far")

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        when, raw = pending
        pending = None
        sender, separator, body = raw.partition(": ")
        if not separator or not sender.strip():
            return  # the system talking
        text, kind, attachment = _classify(body)
        if kind == "texto" and not text:
            return
        messages.append(ChatMessage(sender.strip(), text, when, kind, attachment))

    for raw in content.split("\n"):
        line = _clean(raw)
        match = _IOS.match(line) or _ANDROID.match(line)
        when = None
        if match:
            day, month, year, clock, meridiem, rest = match.groups()
            when = _when(day, month, year, clock, meridiem)
        if match and when is not None:
            flush()
            pending = (when, rest)
        elif pending is not None:
            pending = (pending[0], f"{pending[1]}\n{line}")
    flush()
    return messages


def participants(messages: Sequence[ChatMessage]) -> list[tuple[str, int]]:
    """Who wrote and how much, the most talkative first."""
    return Counter(m.sender for m in messages).most_common()


def conversations(messages: Sequence[ChatMessage]) -> list[list[ChatMessage]]:
    """The chat cut into conversations wherever there was a long silence."""
    groups: list[list[ChatMessage]] = []
    for message in messages:
        if groups and message.when - groups[-1][-1].when <= CONVERSATION_GAP:
            groups[-1].append(message)
        else:
            groups.append([message])
    return groups


def to_turns(messages: Sequence[ChatMessage], sellers: Sequence[str]) -> list[Turn]:
    """The messages with names replaced by roles: the business's people are `vendedor`."""
    mine = set(sellers)
    return [Turn("vendedor" if m.sender in mine else "cliente", m.text, m.kind) for m in messages]


# ---------------------------------------------------------------------- upload


@dataclass(frozen=True)
class Upload:
    messages: list[ChatMessage]
    # The voice notes the zip carries, by name; read on demand, not all in memory at once.
    audio_names: list[str]
    read_audio: Callable[[str], bytes | None]


def _safe(name: str) -> bool:
    return not (name.startswith("/") or ".." in name.split("/") or "\\" in name)


def read_upload(data: bytes) -> Upload:
    """A `.txt` or a `.zip` with the chat and its attachments. Raises `ExportError`."""
    if not data:
        raise ExportError("empty_file", "El archivo está vacío.")
    if data[:2] != b"PK":
        return Upload(_messages(data), [], lambda _name: None)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExportError("bad_zip", "No se pudo abrir el .zip.") from exc
    entries = [e for e in archive.infolist() if not e.is_dir() and _safe(e.filename)]
    if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_UNCOMPRESSED:
        raise ExportError("zip_too_big", "El .zip es demasiado grande para leerlo.")
    texts = [e for e in entries if e.filename.lower().endswith(".txt")]
    if not texts:
        raise ExportError("no_chat", "El .zip no trae el archivo de texto del chat.")
    chat = max(texts, key=lambda e: (e.filename.lower().endswith("_chat.txt"), e.file_size))
    messages = _messages(archive.read(chat))
    members = {e.filename.rsplit("/", 1)[-1]: e for e in entries if _kind_of(e.filename) == "audio"}
    names = [m.attachment for m in messages if m.kind == "audio" and m.attachment in members]

    def read_audio(name: str) -> bytes | None:
        member = members.get(name)
        if member is None or member.file_size > MAX_AUDIO_BYTES:
            return None
        return archive.read(member)

    return Upload(messages, [n for n in names if n], read_audio)


def _messages(raw: bytes) -> list[ChatMessage]:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    else:  # pragma: no cover - latin-1 decodes anything
        raise ExportError("bad_encoding", "No se pudo leer el texto del chat.")
    messages = parse_chat(text)
    if not messages:
        raise ExportError(
            "no_messages", "No se encontraron mensajes: ¿es un chat exportado de WhatsApp?"
        )
    return messages
