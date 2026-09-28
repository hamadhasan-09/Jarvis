"""Speech-to-text provider contracts."""

from __future__ import annotations

from typing import Protocol

from voice_engine.models import Transcript


class STTError(RuntimeError):
    """Controlled STT failure surfaced to the voice engine."""


class STTProvider(Protocol):
    name: str
    model_name: str

    def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate: int,
        channels: int,
        language: str = "auto",
        context: str | None = None,
    ) -> Transcript: ...

    def close(self) -> None: ...
