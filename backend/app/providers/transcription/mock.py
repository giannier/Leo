"""A transcriber for tests and for development without a key."""

from __future__ import annotations

from app.providers.transcription.base import Transcript, TranscriptionError


class MockTranscriptionProvider:
    """Answers with a text per file name; `seconds` is what every audio is said to last."""

    def __init__(self, texts: dict[str, str] | None = None, seconds: float = 12.0) -> None:
        self.texts = texts or {}
        self.seconds = seconds
        self.calls: list[str] = []
        self.fail: set[str] = set()

    async def transcribe(self, data: bytes, *, filename: str, language: str = "es") -> Transcript:
        self.calls.append(filename)
        if filename in self.fail:
            raise TranscriptionError("No se pudo leer el audio.")
        return Transcript(text=self.texts.get(filename, "mensaje de voz"), seconds=self.seconds)
