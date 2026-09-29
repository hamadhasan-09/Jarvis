from __future__ import annotations

import os

from voice_engine.profiles.voice_profile import VoiceProfile
from voice_engine.tts.base import TTSProvider, TTSProviderError
from voice_engine.tts.rumik import RumikWebSocketTTS
from voice_engine.tts.windows_fallback import WindowsFallbackTTS


class FallbackTTSProvider(TTSProvider):
    """Use the configured provider and fall back only before audio is emitted."""

    name = "fallback"

    def __init__(self, primary: TTSProvider, fallback: TTSProvider):
        super().__init__(profile=primary.profile)
        self.primary = primary
        self.fallback = fallback
        self.active: TTSProvider | None = None
        self.name = primary.name
        self.model_name = primary.model_name

    async def connect(self) -> None:
        try:
            await self.primary.connect()
            self.active = self.primary
        except Exception:
            await self.fallback.connect()
            self.active = self.fallback
            self.name = self.fallback.name
            self.model_name = self.fallback.model_name

    async def synthesize_stream(self, text: str, *, generation_id: str):
        if self.active is None:
            await self.connect()
        if self.active is self.fallback:
            async for chunk in self.fallback.synthesize_stream(text, generation_id=generation_id):
                yield chunk
            return

        emitted = False
        try:
            async for chunk in self.primary.synthesize_stream(text, generation_id=generation_id):
                emitted = True
                self.active = self.primary
                self.name = self.primary.name
                self.model_name = self.primary.model_name
                yield chunk
            return
        except Exception as primary_error:
            if emitted:
                raise
            try:
                await self.fallback.connect()
                self.active = self.fallback
                self.name = self.fallback.name
                self.model_name = self.fallback.model_name
                async for chunk in self.fallback.synthesize_stream(text, generation_id=generation_id):
                    yield chunk
            except Exception as fallback_error:
                raise TTSProviderError(
                    f"Primary TTS failed ({primary_error}); fallback failed ({fallback_error})"
                ) from fallback_error

    async def cancel(self, generation_id: str | None = None) -> None:
        await self.primary.cancel(generation_id)
        await self.fallback.cancel(generation_id)

    async def close(self) -> None:
        await self.primary.close()
        await self.fallback.close()
        self.active = None


def create_tts_from_env(*, profile: VoiceProfile | None = None) -> TTSProvider:
    profile = profile or VoiceProfile.from_env()
    provider = os.getenv("JARVIS_TTS_PROVIDER", "rumik").strip().lower()
    fallback_name = os.getenv("JARVIS_TTS_FALLBACK", "windows").strip().lower()
    if provider in {"windows", "system_speech", "system.speech"}:
        primary: TTSProvider = WindowsFallbackTTS(profile=profile)
    elif provider == "rumik":
        primary = RumikWebSocketTTS(profile=profile)
    else:
        raise TTSProviderError(f"Unsupported voice TTS provider: {provider}")
    if fallback_name in {"", "none", "off"}:
        return primary
    if fallback_name in {"windows", "system_speech", "system.speech"}:
        return FallbackTTSProvider(primary, WindowsFallbackTTS(profile=profile))
    raise TTSProviderError(f"Unsupported voice TTS fallback: {fallback_name}")


__all__ = [
    "FallbackTTSProvider",
    "RumikWebSocketTTS",
    "TTSProvider",
    "TTSProviderError",
    "WindowsFallbackTTS",
    "create_tts_from_env",
]
