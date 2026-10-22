"""Builds the transcription provider from the settings (one today: OpenAI)."""

from __future__ import annotations

from app.core.config import Settings
from app.providers.transcription.base import Transcript, TranscriptionError, TranscriptionProvider
from app.providers.transcription.openai import OpenAITranscriptionProvider


class UnavailableTranscription:
    """Without a key every audio is left out; the text of the chat still works."""

    async def transcribe(self, data: bytes, *, filename: str, language: str = "es") -> Transcript:
        raise TranscriptionError("La transcripción no está configurada.")


def build_transcription_provider(settings: Settings) -> TranscriptionProvider:
    if settings.openai_api_key:
        return OpenAITranscriptionProvider(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url
        )
    return UnavailableTranscription()
