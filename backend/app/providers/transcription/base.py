"""The `TranscriptionProvider` port: audio in, text out.

Only for the voice notes of a chat a business imports to teach the assistant (017, C-07). Never
for the messages of a live conversation, which stay «solo leo texto». It costs per minute of
audio, so the caller keeps a ceiling per import and the provider says how long each audio lasted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TranscriptionError(RuntimeError):
    """The audio could not be transcribed (unreadable, too big, the provider failed)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    # What the provider reports as the length of the audio: what it bills by.
    seconds: float


class TranscriptionProvider(Protocol):
    async def transcribe(self, data: bytes, *, filename: str, language: str = "es") -> Transcript:
        """Raises `TranscriptionError`."""
        ...
