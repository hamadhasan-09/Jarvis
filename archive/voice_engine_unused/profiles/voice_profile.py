"""Provider-neutral voice preferences consumed by active TTS adapters."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _optional_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    return float(value) if value else None


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else None


@dataclass(frozen=True)
class VoiceProfile:
    voice: str | None = None
    language: str = "auto"
    speaker: str | None = None
    description: str | None = None
    pitch: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repetition_penalty: float | None = None
    max_new_tokens: int | None = None
    speech_rate: float | None = None

    @classmethod
    def from_env(cls) -> "VoiceProfile":
        return cls(
            voice=os.getenv("JARVIS_VOICE", "").strip() or None,
            language=os.getenv("JARVIS_VOICE_LANGUAGE", os.getenv("JARVIS_STT_LANGUAGE", "auto")).strip() or "auto",
            speaker=os.getenv("JARVIS_VOICE_SPEAKER", os.getenv("RUMIK_SPEAKER", "")).strip() or None,
            description=os.getenv("JARVIS_VOICE_DESCRIPTION", os.getenv("RUMIK_DESCRIPTION", "")).strip() or None,
            pitch=_optional_int("JARVIS_VOICE_PITCH"),
            temperature=_optional_float("JARVIS_VOICE_TEMPERATURE"),
            top_p=_optional_float("JARVIS_VOICE_TOP_P"),
            top_k=_optional_int("JARVIS_VOICE_TOP_K"),
            repetition_penalty=_optional_float("JARVIS_VOICE_REPETITION_PENALTY"),
            max_new_tokens=_optional_int("JARVIS_VOICE_MAX_NEW_TOKENS"),
            speech_rate=_optional_float("JARVIS_VOICE_SPEECH_RATE"),
        )
