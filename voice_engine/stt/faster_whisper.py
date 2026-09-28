"""Local Faster-Whisper STT adapter.

The optional dependency and model are loaded lazily so the existing JARVIS
backend can import the new subsystem without downloading a model.
"""

from __future__ import annotations

import math
import os
from typing import Any

import numpy as np

from voice_engine.models import Transcript, TranscriptSegment
from voice_engine.stt.base import STTError


class FasterWhisperSTT:
    name = "faster_whisper"

    def __init__(
        self,
        *,
        model_name: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        default_language: str = "auto",
        vad_filter: bool = True,
    ):
        self.model_name = str(model_name)
        self.device = str(device)
        self.compute_type = str(compute_type)
        self.default_language = str(default_language or "auto")
        self.vad_filter = bool(vad_filter)
        self._model: Any | None = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise STTError(
                "Faster-Whisper is not installed; install the optional voice dependencies"
            ) from exc
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            raise STTError(f"Faster-Whisper model load failed: {exc}") from exc
        return self._model

    def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate: int,
        channels: int,
        language: str = "auto",
        context: str | None = None,
    ) -> Transcript:
        if channels != 1:
            raise STTError(f"Faster-Whisper adapter expects mono audio, got {channels} channels")
        if sample_rate <= 0 or len(audio) % 2:
            raise STTError("Invalid signed-16 PCM input")
        pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        requested_language = str(language or self.default_language or "auto").strip().lower()
        language_arg = None if requested_language in {"", "auto", "automatic"} else requested_language
        kwargs: dict[str, Any] = {
            "language": language_arg,
            "vad_filter": self.vad_filter,
            "beam_size": int(os.getenv("JARVIS_STT_BEAM_SIZE", "5")),
        }
        if context:
            kwargs["initial_prompt"] = str(context)[:1000]
        try:
            segments, info = self._load().transcribe(pcm, **kwargs)
            materialized = list(segments)
        except Exception as exc:
            raise STTError(f"Faster-Whisper transcription failed: {exc}") from exc

        items: list[TranscriptSegment] = []
        texts: list[str] = []
        for segment in materialized:
            text = str(getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            avg_logprob = getattr(segment, "avg_logprob", None)
            probability = None
            if avg_logprob is not None:
                probability = max(0.0, min(1.0, math.exp(float(avg_logprob))))
            items.append(
                TranscriptSegment(
                    text=text,
                    start_seconds=getattr(segment, "start", None),
                    end_seconds=getattr(segment, "end", None),
                    probability=probability,
                )
            )
            texts.append(text)
        language_name = getattr(info, "language", None)
        language_probability = getattr(info, "language_probability", None)
        return Transcript(
            text=" ".join(texts).strip(),
            language=str(language_name) if language_name else None,
            probability=float(language_probability) if language_probability is not None else None,
            segments=tuple(items),
            provider=self.name,
        )

    def close(self) -> None:
        self._model = None


def create_stt_from_env() -> FasterWhisperSTT:
    provider = os.getenv("JARVIS_STT_PROVIDER", "faster_whisper").strip().lower()
    if provider not in {"faster_whisper", "faster-whisper"}:
        raise STTError(f"Unsupported voice STT provider: {provider}")
    return FasterWhisperSTT(
        model_name=os.getenv("JARVIS_STT_MODEL", "base"),
        device=os.getenv("JARVIS_STT_DEVICE", "cpu"),
        compute_type=os.getenv("JARVIS_STT_COMPUTE_TYPE", "int8"),
        default_language=os.getenv("JARVIS_STT_LANGUAGE", "auto"),
        vad_filter=os.getenv("JARVIS_STT_VAD_FILTER", "1").strip().lower() not in {"0", "false", "no"},
    )
