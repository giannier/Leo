"""OpenAI's transcription endpoint, over plain `httpx` (like the chat adapter).

``POST {base}/audio/transcriptions`` (multipart: ``file``, ``model``, ``language``,
``response_format``) with ``Authorization: Bearer <key>``. With ``response_format=verbose_json`` the
answer carries ``text`` and ``duration`` (seconds), which is what the ceiling per import counts.
Model ``whisper-1`` (priced per minute of audio). WhatsApp voice notes are Opus in an Ogg container
(``.opus``): they are sent as ``.ogg``, which is the extension the endpoint recognises.

Verified from the public documentation only: the file formats (mp3, mp4, mpeg, mpga, m4a, wav,
webm, ogg, flac), the 25 MB limit per file and the ``duration`` of ``verbose_json`` for
``whisper-1``. Not run against the real endpoint with a real voice note yet.
"""

from __future__ import annotations

import httpx

from app.providers.transcription.base import Transcript, TranscriptionError

MAX_BYTES = 25 * 1024 * 1024
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
MIME_BY_EXTENSION = {
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "oga": "audio/ogg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "amr": "audio/amr",
}


def upload_name(filename: str) -> tuple[str, str]:
    """The name and type to upload a voice note under: Opus goes as Ogg."""
    base, _, extension = filename.rpartition(".")
    extension = extension.lower()
    if extension in ("opus", "oga"):
        extension = "ogg"
    return f"{base or 'audio'}.{extension or 'ogg'}", MIME_BY_EXTENSION.get(extension, "audio/ogg")


class OpenAITranscriptionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "whisper-1",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._model = model
        self._transport = transport

    async def transcribe(self, data: bytes, *, filename: str, language: str = "es") -> Transcript:
        if len(data) > MAX_BYTES:
            raise TranscriptionError("El audio pesa más de 25 MB.")
        name, mime = upload_name(filename)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data={
                        "model": self._model,
                        "language": language,
                        "response_format": "verbose_json",
                    },
                    files={"file": (name, data, mime)},
                )
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                "No se pudo conectar con el servicio de transcripción."
            ) from exc
        if response.status_code >= 400:
            raise TranscriptionError(
                f"El servicio de transcripción respondió {response.status_code}."
            )
        try:
            body = response.json()
            text = str(body["text"]).strip()
            seconds = float(body.get("duration") or 0)
        except (ValueError, KeyError, TypeError) as exc:
            raise TranscriptionError("La respuesta de la transcripción no se entiende.") from exc
        return Transcript(text=text, seconds=seconds)
