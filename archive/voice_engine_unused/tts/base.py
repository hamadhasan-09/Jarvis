"""Text-to-speech provider contracts for streaming and cancellation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from voice_engine.models import AudioChunk
from voice_engine.profiles.voice_profile import VoiceProfile


class TTSProviderError(RuntimeError):
    """Controlled TTS provider failure."""


class TTSProvider(ABC):
    name = "unknown"
    model_name = "unknown"

    def __init__(self, *, profile: VoiceProfile | None = None):
        self.profile = profile or VoiceProfile.from_env()

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def synthesize_stream(
        self, text: str, *, generation_id: str
    ) -> AsyncIterator[AudioChunk]:
        ...

    @abstractmethod
    async def cancel(self, generation_id: str | None = None) -> None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    async def synthesize(self, text: str, *, generation_id: str) -> list[AudioChunk]:
        chunks: list[AudioChunk] = []
        async for chunk in self.synthesize_stream(text, generation_id=generation_id):
            chunks.append(chunk)
        return chunks
